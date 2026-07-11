"""Atomic content cache and transaction-backed campaign checkpoints."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence

from .canonical import canonical_json, content_hash, file_sha256
from .model import CandidateRecord, EvaluationResult, EvaluationStatus, ScoredEvaluationResult
from .serde import candidate_from_dict, candidate_to_dict, result_from_dict, result_to_dict


CACHE_SCHEMA = 3
CAMPAIGN_SCHEMA = 3
CHECKPOINT_SCHEMA = 3
EXTRACTION_SCHEMA = 3


def _state_error(kind: str, found: Any) -> ValueError:
    return ValueError(
        f"unsupported {kind} schema {found}; outer-loop state schemas before 3 are intentionally "
        "incompatible with schema 3. Choose a fresh run/cache directory. Existing state "
        "was left untouched and is evidence only."
    )


def _existing_schema(path: Path) -> str | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        return None if row is None else str(row[0])
    except sqlite3.DatabaseError:
        return "unknown"
    finally:
        connection.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        last_error: PermissionError | None = None
        for attempt in range(25):
            try:
                os.replace(temporary, target)
                last_error = None
                break
            except PermissionError as error:
                last_error = error
                time.sleep(min(0.25, 0.01 * (attempt + 1)))
        if last_error is not None:
            raise last_error
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


class ArtifactStore:
    """Immutable SHA-256 file store used by evaluation records."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, source: str | Path) -> tuple[Path, str]:
        source_path = Path(source).resolve()
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
        digest = file_sha256(source_path)
        target = self.root / digest[:2] / digest / source_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(target.with_suffix(target.suffix + ".lock")):
            if target.is_file():
                if file_sha256(target) != digest:
                    raise ValueError(f"artifact hash collision at {target}")
                return target, digest
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output, source_path.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                if file_sha256(temporary) != digest:
                    raise OSError(f"artifact changed while being copied: {source_path}")
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return target, digest


@contextmanager
def exclusive_file_lock(path: str | Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {utc_now()}\n".encode("utf-8"))
        except FileExistsError:
            try:
                stale = time.time() - lock_path.stat().st_mtime > max(300.0, timeout_seconds * 2.0)
            except FileNotFoundError:
                continue
            if stale:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for cache lock {lock_path}")
            time.sleep(0.01)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _context_differences(
    stored: Mapping[str, Any], requested: Mapping[str, Any], prefix: str = ""
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []
    for key in sorted(set(stored) | set(requested)):
        path = f"{prefix}.{key}" if prefix else key
        left = stored.get(key, {"$missing": True})
        right = requested.get(key, {"$missing": True})
        if isinstance(left, Mapping) and isinstance(right, Mapping):
            differences.extend(_context_differences(left, right, path))
        elif canonical_json(left) != canonical_json(right):
            differences.append({"field": path, "cached": left, "requested": right})
    return differences


def _immutable_result_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove lineage/attempt annotations that are not evaluation content."""
    normalized = dict(value)
    normalized.pop("candidate_id", None)
    provenance = dict(normalized.get("provenance", {}))
    provenance.pop("worker_attempts", None)
    normalized["provenance"] = provenance
    return normalized


def _association_from_json(text: str) -> EvaluationResult:
    result = result_from_dict(json.loads(text))
    if (
        isinstance(result, ScoredEvaluationResult)
        and result.scoring_context.get("compatibility") == "unscored"
    ):
        return result.raw()
    return result


class EvaluationCache:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.entries = self.root / "entries"
        self.database_path = self.root / "cache.sqlite"
        existing = _existing_schema(self.database_path)
        if existing is not None and existing != str(CACHE_SCHEMA):
            raise _state_error("cache", existing)
        self.entries.mkdir(parents=True, exist_ok=True)
        with _connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS entries(
                    evaluation_key TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    fidelity TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_path TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS entries_candidate ON entries(candidate_id, fidelity);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(CACHE_SCHEMA),),
            )

    def _entry_path(self, evaluation_key: str) -> Path:
        return self.entries / evaluation_key[:2] / f"{evaluation_key}.json"

    def get(self, evaluation_key: str) -> EvaluationResult | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT result_path FROM entries WHERE evaluation_key = ?", (evaluation_key,)
            ).fetchone()
        if row is None:
            return None
        path = self.root / row["result_path"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if data.get("schema_version") != CACHE_SCHEMA:
            raise _state_error("cache entry", data.get("schema_version"))
        return result_from_dict(data["result"])

    def put(self, result: EvaluationResult, context: Mapping[str, Any]) -> Path:
        if isinstance(result, ScoredEvaluationResult):
            raise TypeError("the evaluation cache accepts raw EvaluationResult values only")
        if result.status in {EvaluationStatus.PENDING, EvaluationStatus.RUNNING, EvaluationStatus.CANCELLED}:
            raise ValueError(f"status {result.status.value} is not a completed cache result")
        path = self._entry_path(result.evaluation_key)
        payload = {
            "schema_version": CACHE_SCHEMA,
            "result": result_to_dict(result),
            "context": dict(context),
            "written_at": utc_now(),
        }
        with exclusive_file_lock(path.with_suffix(".lock")):
            if path.is_file():
                existing = json.loads(path.read_text(encoding="utf-8"))
                if canonical_json(_immutable_result_payload(existing.get("result", {}))) != canonical_json(_immutable_result_payload(payload["result"])):
                    fields = _context_differences(
                        _immutable_result_payload(existing.get("result", {})),
                        _immutable_result_payload(payload["result"]),
                    )
                    raise ValueError(
                        f"immutable cache conflict for evaluation key {result.evaluation_key}: {fields}"
                    )
            else:
                atomic_write_json(path, payload)
            relative = path.relative_to(self.root).as_posix()
            with _connect(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO entries(
                        evaluation_key, candidate_id, fidelity, status, result_path, context_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.evaluation_key,
                        result.candidate_id,
                        result.fidelity,
                        result.status.value,
                        relative,
                        canonical_json(context),
                        utc_now(),
                    ),
                )
        return path

    def explain(
        self,
        evaluation_key: str,
        candidate_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            exact = connection.execute(
                "SELECT * FROM entries WHERE evaluation_key = ?", (evaluation_key,)
            ).fetchone()
            related = []
            if candidate_id:
                related = connection.execute(
                    "SELECT evaluation_key, fidelity, status, context_json, created_at FROM entries WHERE candidate_id = ? ORDER BY created_at",
                    (candidate_id,),
                ).fetchall()
        related_contexts = []
        for row in related:
            item = dict(row)
            stored = json.loads(item.pop("context_json"))
            item["context"] = stored
            if context is not None:
                item["field_differences"] = _context_differences(stored, context)
            related_contexts.append(item)
        exact_value = dict(exact) if exact is not None else None
        if exact_value is not None:
            stored_exact = json.loads(exact_value.pop("context_json"))
            exact_value["context"] = stored_exact
            if context is not None:
                exact_value["field_differences"] = _context_differences(stored_exact, context)
        return {
            "hit": exact is not None,
            "exact": exact_value,
            "related_contexts": related_contexts,
            "reason": "exact evaluation context matched" if exact is not None else "no exact evaluation context matched",
        }


class CampaignStore:
    def __init__(
        self, run_directory: str | Path, checkpoint_directory: str | Path | None = None
    ):
        self.run_directory = Path(run_directory).resolve()
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.run_directory / "campaign.sqlite"
        checkpoint_root = Path(checkpoint_directory).resolve() if checkpoint_directory else self.run_directory
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = checkpoint_root / "checkpoint.json"
        existing = _existing_schema(self.database_path)
        if existing is not None and existing != str(CAMPAIGN_SCHEMA):
            raise _state_error("campaign", existing)
        if self.checkpoint_path.is_file():
            try:
                checkpoint_schema = json.loads(self.checkpoint_path.read_text(encoding="utf-8")).get("schema_version")
            except (OSError, json.JSONDecodeError):
                checkpoint_schema = "unknown"
            if checkpoint_schema != CHECKPOINT_SCHEMA and existing is None:
                raise _state_error("checkpoint", checkpoint_schema)
        with _connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS generation_candidates(
                    trial INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    individual_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    scored_result_json TEXT,
                    evaluation_key TEXT,
                    PRIMARY KEY(trial, generation, role, position)
                );
                DROP INDEX IF EXISTS generation_individual;
                CREATE INDEX IF NOT EXISTS generation_individual_lookup ON generation_candidates(individual_id, role);
                CREATE TABLE IF NOT EXISTS evaluations(
                    evaluation_key TEXT PRIMARY KEY,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fidelity TEXT NOT NULL,
                    raw_result_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scored_evaluations(
                    association_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_key TEXT NOT NULL REFERENCES evaluations(evaluation_key),
                    candidate_id TEXT NOT NULL,
                    fidelity TEXT NOT NULL,
                    scoring_context_hash TEXT NOT NULL,
                    scored_result_json TEXT NOT NULL,
                    associated_at TEXT NOT NULL,
                    UNIQUE(evaluation_key, scoring_context_hash)
                );
                CREATE TABLE IF NOT EXISTS evaluation_attempts(
                    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    evaluation_key TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    fidelity TEXT NOT NULL,
                    raw_result_json TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS archive(
                    comparison_context_id TEXT NOT NULL,
                    trial INTEGER NOT NULL,
                    fidelity TEXT NOT NULL,
                    evaluation_key TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    first_generation INTEGER NOT NULL,
                    objective_json TEXT NOT NULL,
                    scored_result_json TEXT NOT NULL,
                    PRIMARY KEY(comparison_context_id, trial, fidelity, evaluation_key)
                );
                CREATE TABLE IF NOT EXISTS operator_statistics(
                    trial INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    operator TEXT NOT NULL,
                    proposed INTEGER NOT NULL DEFAULT 0,
                    effective INTEGER NOT NULL DEFAULT 0,
                    no_op INTEGER NOT NULL DEFAULT 0,
                    rejected INTEGER NOT NULL DEFAULT 0,
                    accepted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(trial, generation, operator)
                );
                CREATE TABLE IF NOT EXISTS promotions(
                    source_fidelity TEXT NOT NULL,
                    target_fidelity TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    source_evaluation_key TEXT,
                    target_evaluation_key TEXT NOT NULL,
                    target_status TEXT NOT NULL,
                    target_result_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY(source_fidelity, target_fidelity, candidate_id, target_evaluation_key)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(CAMPAIGN_SCHEMA),),
            )
            schema = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0]
            if int(schema) != CAMPAIGN_SCHEMA:
                raise _state_error("campaign", schema)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = _connect(self.database_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def set_metadata(self, key: str, value: Any) -> None:
        with _connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES(?, ?)",
                (key, json.dumps(value, sort_keys=True, allow_nan=False)),
            )

    def get_metadata(self, key: str, default: Any = None) -> Any:
        with _connect(self.database_path) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def save_candidates(
        self,
        trial: int,
        generation: int,
        role: str,
        candidates: Sequence[CandidateRecord],
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM generation_candidates WHERE trial=? AND generation=? AND role=?",
                (trial, generation, role),
            )
            for position, candidate in enumerate(candidates):
                connection.execute(
                    """
                    INSERT INTO generation_candidates(
                        trial, generation, role, position, individual_id, candidate_id, candidate_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trial,
                        generation,
                        role,
                        position,
                        candidate.individual_id,
                        candidate.candidate_id,
                        json.dumps(candidate_to_dict(candidate), sort_keys=True, allow_nan=False),
                    ),
                )

    def save_result(
        self,
        trial: int,
        generation: int,
        role: str,
        position: int | Sequence[int],
        result: ScoredEvaluationResult,
    ) -> None:
        if not isinstance(result, ScoredEvaluationResult):
            # Public compatibility: a caller that has no campaign scoring
            # semantics still creates an explicit, empty scored association.
            result = ScoredEvaluationResult.from_raw(
                result,
                aggregate_violation=result.solver_violation,
                campaign_feasible=result.status is EvaluationStatus.FEASIBLE,
                scoring_context={"schema_version": 3, "compatibility": "unscored"},
            )
        result_json = json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False)
        raw = result.raw()
        raw_json = json.dumps(result_to_dict(raw), sort_keys=True, allow_nan=False)
        scoring_hash = content_hash(result.scoring_context, prefix="outerloop-scoring-v3")
        positions = (position,) if isinstance(position, int) else tuple(position)
        if not positions:
            raise ValueError("at least one generation position is required")
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT raw_result_json FROM evaluations WHERE evaluation_key=?",
                (result.evaluation_key,),
            ).fetchone()
            if (
                existing is not None
                and canonical_json(_immutable_result_payload(json.loads(existing["raw_result_json"])))
                != canonical_json(_immutable_result_payload(json.loads(raw_json)))
            ):
                raise ValueError(f"immutable evaluation conflict for key {result.evaluation_key}")
            for selected_position in positions:
                connection.execute(
                    """
                    UPDATE generation_candidates SET scored_result_json=?, evaluation_key=?
                    WHERE trial=? AND generation=? AND role=? AND position=?
                    """,
                    (result_json, result.evaluation_key, trial, generation, role, selected_position),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluations(
                    evaluation_key, candidate_id, status, fidelity, raw_result_json, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.evaluation_key,
                    result.candidate_id,
                    raw.status.value,
                    raw.fidelity,
                    raw_json,
                    utc_now(),
                ),
            )
            if connection.execute(
                "SELECT 1 FROM evaluation_attempts WHERE evaluation_key=? LIMIT 1",
                (result.evaluation_key,),
            ).fetchone() is None:
                connection.execute(
                    """INSERT INTO evaluation_attempts(
                        evaluation_key, candidate_id, fidelity, raw_result_json, completed_at
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (raw.evaluation_key, raw.candidate_id, raw.fidelity, raw_json, utc_now()),
                )
            connection.execute(
                """
                INSERT OR REPLACE INTO scored_evaluations(
                    evaluation_key, candidate_id, fidelity, scoring_context_hash,
                    scored_result_json, associated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (result.evaluation_key, result.candidate_id, result.fidelity, scoring_hash, result_json, utc_now()),
            )

    def record_evaluation(self, result: EvaluationResult) -> None:
        raw = result.raw() if isinstance(result, ScoredEvaluationResult) else result
        result_json = json.dumps(result_to_dict(raw), sort_keys=True, allow_nan=False)
        with _connect(self.database_path) as connection:
            existing = connection.execute(
                "SELECT raw_result_json FROM evaluations WHERE evaluation_key=?",
                (result.evaluation_key,),
            ).fetchone()
            if (
                existing is not None
                and canonical_json(_immutable_result_payload(json.loads(existing["raw_result_json"])))
                != canonical_json(_immutable_result_payload(json.loads(result_json)))
            ):
                raise ValueError(f"immutable evaluation conflict for key {result.evaluation_key}")
            connection.execute(
                """
                INSERT OR IGNORE INTO evaluations(
                    evaluation_key, candidate_id, status, fidelity, raw_result_json, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    result.evaluation_key,
                    result.candidate_id,
                    raw.status.value,
                    raw.fidelity,
                    result_json,
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO evaluation_attempts(
                    evaluation_key, candidate_id, fidelity, raw_result_json, completed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (raw.evaluation_key, raw.candidate_id, raw.fidelity, result_json, utc_now()),
            )
            if isinstance(result, ScoredEvaluationResult):
                scoring_hash = content_hash(result.scoring_context, prefix="outerloop-scoring-v3")
                connection.execute(
                    """INSERT OR REPLACE INTO scored_evaluations(
                        evaluation_key, candidate_id, fidelity, scoring_context_hash,
                        scored_result_json, associated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (result.evaluation_key, result.candidate_id, result.fidelity, scoring_hash,
                     json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False), utc_now()),
                )

    def save_population_results(
        self,
        trial: int,
        generation: int,
        role: str,
        results: Sequence[ScoredEvaluationResult],
    ) -> None:
        """Persist a selected population in one durable transaction."""
        with self.transaction() as connection:
            for position, result in enumerate(results):
                if not isinstance(result, ScoredEvaluationResult):
                    raise TypeError("population results require ScoredEvaluationResult")
                result_json = json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False)
                raw = result.raw()
                raw_json = json.dumps(result_to_dict(raw), sort_keys=True, allow_nan=False)
                existing = connection.execute(
                    "SELECT raw_result_json FROM evaluations WHERE evaluation_key=?",
                    (result.evaluation_key,),
                ).fetchone()
                if (
                    existing is not None
                    and canonical_json(_immutable_result_payload(json.loads(existing["raw_result_json"])))
                    != canonical_json(_immutable_result_payload(json.loads(raw_json)))
                ):
                    raise ValueError(f"immutable evaluation conflict for key {result.evaluation_key}")
                connection.execute(
                    """UPDATE generation_candidates SET scored_result_json=?, evaluation_key=?
                       WHERE trial=? AND generation=? AND role=? AND position=?""",
                    (result_json, result.evaluation_key, trial, generation, role, position),
                )
                connection.execute(
                    """INSERT OR IGNORE INTO evaluations(
                       evaluation_key, candidate_id, status, fidelity, raw_result_json, completed_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (raw.evaluation_key, raw.candidate_id, raw.status.value, raw.fidelity, raw_json, utc_now()),
                )
                if connection.execute(
                    "SELECT 1 FROM evaluation_attempts WHERE evaluation_key=? LIMIT 1",
                    (raw.evaluation_key,),
                ).fetchone() is None:
                    connection.execute(
                        """INSERT INTO evaluation_attempts(
                           evaluation_key, candidate_id, fidelity, raw_result_json, completed_at
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (raw.evaluation_key, raw.candidate_id, raw.fidelity, raw_json, utc_now()),
                    )
                scoring_hash = content_hash(result.scoring_context, prefix="outerloop-scoring-v3")
                connection.execute(
                    """INSERT OR REPLACE INTO scored_evaluations(
                       evaluation_key, candidate_id, fidelity, scoring_context_hash,
                       scored_result_json, associated_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (result.evaluation_key, result.candidate_id, result.fidelity, scoring_hash,
                     result_json, utc_now()),
                )

    def load_candidates(
        self, trial: int, generation: int, role: str
    ) -> list[tuple[CandidateRecord, EvaluationResult | None]]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT candidate_json, scored_result_json FROM generation_candidates
                WHERE trial=? AND generation=? AND role=? ORDER BY position
                """,
                (trial, generation, role),
            ).fetchall()
        return [
            (
                candidate_from_dict(json.loads(row["candidate_json"])),
                _association_from_json(row["scored_result_json"]) if row["scored_result_json"] else None,
            )
            for row in rows
        ]

    def evaluation(self, evaluation_key: str) -> EvaluationResult | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT raw_result_json FROM evaluations WHERE evaluation_key=?", (evaluation_key,)
            ).fetchone()
        return result_from_dict(json.loads(row["raw_result_json"])) if row else None

    def evaluation_attempt_count(self, evaluation_key: str) -> int:
        with _connect(self.database_path) as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM evaluation_attempts WHERE evaluation_key=?",
                (evaluation_key,),
            ).fetchone()[0])

    def checkpoint(self, state: Mapping[str, Any]) -> None:
        payload = {
            "schema_version": CHECKPOINT_SCHEMA,
            "database": self.database_path.name,
            "run_directory": str(self.run_directory),
            "updated_at": utc_now(),
            **dict(state),
        }
        self.set_metadata("checkpoint", payload)
        atomic_write_json(self.checkpoint_path, payload)

    def load_checkpoint(self) -> dict[str, Any] | None:
        database_value = self.get_metadata("checkpoint")
        file_value = None
        if self.checkpoint_path.is_file():
            try:
                file_value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                file_value = None
        state = database_value or file_value
        if state and int(state.get("schema_version", -1)) != CHECKPOINT_SCHEMA:
            raise _state_error("checkpoint", state.get("schema_version"))
        return state

    def archive_replace(
        self,
        comparison_context_id: str,
        trial: int,
        fidelity: str,
        entries: Sequence[tuple[ScoredEvaluationResult, tuple[float, ...], int]],
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM archive WHERE comparison_context_id=? AND trial=? AND fidelity=?",
                (comparison_context_id, trial, fidelity),
            )
            for result, objectives, generation in entries:
                if not isinstance(result, ScoredEvaluationResult):
                    raise TypeError("archive entries require ScoredEvaluationResult")
                connection.execute(
                    """
                    INSERT INTO archive(comparison_context_id, trial, fidelity, evaluation_key, candidate_id, first_generation, objective_json, scored_result_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (comparison_context_id, trial, fidelity, result.evaluation_key, result.candidate_id, generation, json.dumps(objectives),
                     json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False)),
                )
        safe_fidelity = "".join(character if character.isalnum() or character in "-_" else "_" for character in fidelity)
        physical = self.run_directory / "archives" / comparison_context_id / f"trial-{trial}-{safe_fidelity}.jsonl"
        atomic_write_text(
            physical,
            "".join(
                json.dumps(
                    {"schema_version": 3, "comparison_context_id": comparison_context_id,
                     "trial": trial, "fidelity": fidelity, "generation": generation,
                     "objectives": objectives, "result": result_to_dict(result)},
                    sort_keys=True, allow_nan=False,
                ) + "\n"
                for result, objectives, generation in entries
            ),
        )

    def increment_operator(
        self,
        trial: int,
        generation: int,
        operator: str,
        *,
        proposed: int = 0,
        effective: int = 0,
        no_op: int = 0,
        rejected: int = 0,
        accepted: int = 0,
    ) -> None:
        with _connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO operator_statistics(trial, generation, operator, proposed, effective, no_op, rejected, accepted)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trial, generation, operator) DO UPDATE SET
                    proposed=proposed + excluded.proposed,
                    effective=effective + excluded.effective,
                    no_op=no_op + excluded.no_op,
                    rejected=rejected + excluded.rejected,
                    accepted=accepted + excluded.accepted
                """,
                (trial, generation, operator, proposed, effective, no_op, rejected, accepted),
            )

    def operator_statistics(self) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM operator_statistics ORDER BY trial, generation, operator"
            ).fetchall()
        return [dict(row) for row in rows]

    def record_promotion(
        self,
        source_fidelity: str,
        target_fidelity: str,
        source_evaluation_key: str | None,
        result: ScoredEvaluationResult,
    ) -> None:
        with _connect(self.database_path) as connection:
            connection.execute(
                """INSERT OR REPLACE INTO promotions(
                    source_fidelity, target_fidelity, candidate_id, source_evaluation_key,
                    target_evaluation_key, target_status, target_result_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (source_fidelity, target_fidelity, result.candidate_id, source_evaluation_key,
                 result.evaluation_key, result.status.value,
                 json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False), utc_now()),
            )

    def load_archive(
        self, comparison_context_id: str, trial: int, fidelity: str
    ) -> list[tuple[ScoredEvaluationResult, tuple[float, ...], int]]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT a.objective_json, a.first_generation, a.scored_result_json
                FROM archive a
                WHERE a.comparison_context_id=? AND a.trial=? AND a.fidelity=?
                ORDER BY a.objective_json, a.candidate_id, a.evaluation_key
                """,
                (comparison_context_id, trial, fidelity),
            ).fetchall()
        return [
            (
                ScoredEvaluationResult.from_dict(json.loads(row["scored_result_json"])),
                tuple(float(value) for value in json.loads(row["objective_json"])),
                int(row["first_generation"]),
            )
            for row in rows
        ]

    def status(self) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            counts = {
                row["status"]: row["count"]
                for row in connection.execute("SELECT status, COUNT(*) AS count FROM evaluations GROUP BY status")
            }
            scored_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT json_extract(scored_result_json, '$.status') AS status, COUNT(*) AS count FROM scored_evaluations GROUP BY status"
                )
            }
            archive_count = connection.execute("SELECT COUNT(*) FROM archive").fetchone()[0]
        return {
            "run_directory": str(self.run_directory),
            "checkpoint": self.load_checkpoint(),
            "evaluation_counts": counts,
            "scored_association_counts": scored_counts,
            "archive_count": archive_count,
            "operator_statistics": self.operator_statistics(),
        }

    def find_candidate(
        self, identifier: str, fidelity: str | None = None, trial: int | None = None
    ) -> tuple[CandidateRecord, EvaluationResult | None] | None:
        if fidelity is None:
            resolved = self.get_metadata("resolved_configuration", {})
            ladder = sorted(resolved.get("fidelities", ()), key=lambda value: int(value.get("rank", 0)))
            if ladder:
                fidelity = "confirmed" if any(value.get("name") == "confirmed" for value in ladder) else str(ladder[-1]["name"])
        clauses = ["(individual_id=? OR candidate_id=?)"]
        parameters: list[Any] = [identifier, identifier]
        if fidelity is not None:
            clauses.append("json_extract(scored_result_json, '$.fidelity')=?")
            parameters.append(fidelity)
        if trial is not None:
            clauses.append("trial=?")
            parameters.append(trial)
        with _connect(self.database_path) as connection:
            row = connection.execute(
                f"""
                SELECT candidate_json, scored_result_json FROM generation_candidates
                WHERE {' AND '.join(clauses)}
                ORDER BY trial, generation, role, position LIMIT 1
                """,
                tuple(parameters),
            ).fetchone()
        if row is None:
            return None
        return (
            candidate_from_dict(json.loads(row["candidate_json"])),
            _association_from_json(row["scored_result_json"]) if row["scored_result_json"] else None,
        )

    def generation_records(self) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT trial, generation, role, position, candidate_json, scored_result_json, evaluation_key
                FROM generation_candidates ORDER BY trial, generation, role, position
                """
            ).fetchall()
        output = []
        for row in rows:
            output.append({
                "trial": row["trial"],
                "generation": row["generation"],
                "role": row["role"],
                "position": row["position"],
                "candidate": candidate_from_dict(json.loads(row["candidate_json"])),
                "result": _association_from_json(row["scored_result_json"]) if row["scored_result_json"] else None,
                "evaluation_key": row["evaluation_key"],
            })
        return output

    def archive_records(
        self,
        fidelity: str | None = None,
        *,
        trial: int | None = None,
        comparison_context_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT a.comparison_context_id, a.trial, a.fidelity, a.first_generation, a.objective_json, a.scored_result_json
            FROM archive a
        """
        clauses = []
        parameters: list[Any] = []
        if fidelity is not None:
            clauses.append("a.fidelity=?")
            parameters.append(fidelity)
        if trial is not None:
            clauses.append("a.trial=?")
            parameters.append(trial)
        if comparison_context_id is not None:
            clauses.append("a.comparison_context_id=?")
            parameters.append(comparison_context_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY a.comparison_context_id, a.trial, a.fidelity, a.objective_json, a.candidate_id, a.evaluation_key"
        with _connect(self.database_path) as connection:
            rows = connection.execute(query, tuple(parameters)).fetchall()
        output = []
        for row in rows:
            result = ScoredEvaluationResult.from_dict(json.loads(row["scored_result_json"]))
            found = self.find_candidate(result.candidate_id, row["fidelity"], int(row["trial"]))
            output.append({
                "fidelity": row["fidelity"],
                "comparison_context_id": row["comparison_context_id"],
                "trial": row["trial"],
                "generation": row["first_generation"],
                "objectives": tuple(json.loads(row["objective_json"])),
                "result": result,
                "candidate": found[0] if found else None,
            })
        return output

    def evaluation_records(self, fidelity: str | None = None) -> list[dict[str, Any]]:
        """Return every raw evaluation and every scored campaign association.

        This is the reporting source for reruns and fidelity confirmations; it
        deliberately does not depend on generation membership.
        """
        query = """
            SELECT a.raw_result_json, s.scored_result_json
            FROM evaluation_attempts a
            LEFT JOIN scored_evaluations s ON s.evaluation_key=a.evaluation_key
        """
        parameters: tuple[Any, ...] = ()
        if fidelity is not None:
            query += " WHERE a.fidelity=?"
            parameters = (fidelity,)
        query += " ORDER BY a.completed_at, a.attempt_id, s.association_id"
        with _connect(self.database_path) as connection:
            rows = connection.execute(query, parameters).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            raw = result_from_dict(json.loads(row["raw_result_json"]))
            scored = (
                ScoredEvaluationResult.from_dict(json.loads(row["scored_result_json"]))
                if row["scored_result_json"] else None
            )
            scored_context = scored.scoring_context if scored is not None else {}
            record_trial = scored_context.get("trial")
            found = self.find_candidate(
                raw.candidate_id,
                raw.fidelity,
                int(record_trial) if record_trial is not None else None,
            )
            records.append({
                "candidate": found[0] if found else None,
                "result": scored or raw,
                "raw_result": raw,
                "fidelity": raw.fidelity,
                "comparison_context_id": scored_context.get("comparison_context_id"),
                "trial": record_trial,
                "objectives": tuple((scored or ScoredEvaluationResult.from_raw(raw)).objectives.values()),
            })
        return records

    def metadata_items(self, prefix: str = "") -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT key, value FROM metadata WHERE key LIKE ? ORDER BY key", (prefix + "%",)
            ).fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}
