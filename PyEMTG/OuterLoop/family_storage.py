"""Run-local durable state for feasibility-family continuation workers."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping, Sequence

from .atlas import (
    BranchDefinition,
    ContinuationEdge,
    FamilyAttempt,
    FamilySample,
    SampleClassification,
)
from .family_multidimensional import (
    BoundaryProduct,
    CoverageSnapshot,
    PairSliceDefinition,
    QuadtreeCell,
    RayState,
)
from .canonical import canonical_json, content_hash
from .model import CandidateRecord, EvaluationResult
from .serde import (
    candidate_from_dict,
    candidate_to_dict,
    result_from_dict,
    result_to_dict,
)
from .storage import atomic_write_json, utc_now


FAMILY_RUN_SCHEMA = 2
FAMILY_CHECKPOINT_SCHEMA = 1


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _existing_schema(path: Path) -> int | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='schema_version'"
        ).fetchone()
        return None if row is None else int(row[0])
    except (sqlite3.DatabaseError, TypeError, ValueError):
        return -1
    finally:
        connection.close()


@dataclass(frozen=True)
class ChainState:
    chain_id: str
    axis_ordinal: int
    axis_key: str
    direction: int
    rank: int
    state: str
    current_feasible_sample_key: str
    previous_feasible_sample_key: str | None = None
    not_found_sample_key: str | None = None
    successful_scouts: int = 0
    refinement_count: int = 0
    terminal_reason: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in {"bound_feasible", "bracketed", "blocked_unknown"}

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ChainState":
        return cls(
            str(row["chain_id"]), int(row["axis_ordinal"]), str(row["axis_key"]),
            int(row["direction"]), int(row["rank"]), str(row["state"]),
            str(row["current_feasible_sample_key"]), row["previous_feasible_sample_key"],
            row["not_found_sample_key"], int(row["successful_scouts"]),
            int(row["refinement_count"]), row["terminal_reason"],
        )


@dataclass(frozen=True)
class StoredTask:
    sample: FamilySample
    chain_id: str
    epoch: int
    chain_sequence: int
    purpose: str
    grid_index: int
    task: Mapping[str, Any]
    state: str = "planned"
    prepared_case: Mapping[str, Any] | None = None

    @property
    def sample_key(self) -> str:
        return self.sample.sample_key


class FamilyRunStore:
    """SQLite is authoritative; the JSON checkpoint is an atomic mirror."""

    def __init__(self, run_directory: str | Path):
        self.run_directory = Path(run_directory).resolve()
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.database_path = self.run_directory / "family.sqlite"
        self.checkpoint_path = self.run_directory / "family-checkpoint.json"
        existing = _existing_schema(self.database_path)
        if existing not in {None, 1, FAMILY_RUN_SCHEMA}:
            raise ValueError(
                f"unsupported family run schema {existing}; choose a fresh run directory"
            )
        with _connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS epochs(
                    epoch INTEGER PRIMARY KEY CHECK(epoch >= 0),
                    state TEXT NOT NULL CHECK(state IN ('planned','running','closed')),
                    plan_hash TEXT NOT NULL,
                    planned_count INTEGER NOT NULL CHECK(planned_count >= 0),
                    completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count >= 0),
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    closed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS chains(
                    chain_id TEXT PRIMARY KEY,
                    axis_ordinal INTEGER NOT NULL CHECK(axis_ordinal >= 0),
                    axis_key TEXT NOT NULL,
                    direction INTEGER NOT NULL CHECK(direction IN (-1,1)),
                    rank INTEGER NOT NULL UNIQUE CHECK(rank >= 0),
                    state TEXT NOT NULL CHECK(state IN (
                        'scouting','refining','bound_feasible','bracketed','blocked_unknown'
                    )),
                    current_feasible_sample_key TEXT NOT NULL,
                    previous_feasible_sample_key TEXT,
                    not_found_sample_key TEXT,
                    successful_scouts INTEGER NOT NULL DEFAULT 0 CHECK(successful_scouts >= 0),
                    refinement_count INTEGER NOT NULL DEFAULT 0 CHECK(refinement_count >= 0),
                    terminal_reason TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(axis_key,direction)
                );
                CREATE TABLE IF NOT EXISTS samples(
                    sample_key TEXT PRIMARY KEY,
                    chain_id TEXT REFERENCES chains(chain_id),
                    epoch INTEGER NOT NULL REFERENCES epochs(epoch),
                    chain_sequence INTEGER NOT NULL CHECK(chain_sequence >= 0),
                    purpose TEXT NOT NULL CHECK(purpose IN ('anchor','scout','refine')),
                    grid_index INTEGER,
                    state TEXT NOT NULL CHECK(state IN ('planned','running','completed')),
                    parent_sample_key TEXT REFERENCES samples(sample_key),
                    sample_json TEXT NOT NULL,
                    task_json TEXT NOT NULL,
                    prepared_case_json TEXT,
                    outcome_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(epoch,chain_sequence),
                    UNIQUE(chain_id,grid_index)
                );
                CREATE INDEX IF NOT EXISTS samples_epoch_state
                    ON samples(epoch,state,chain_id,sample_key);
                CREATE TABLE IF NOT EXISTS attempts(
                    sample_key TEXT NOT NULL REFERENCES samples(sample_key) ON DELETE CASCADE,
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
                    rescue_stage_ordinal INTEGER CHECK(rescue_stage_ordinal >= 0),
                    evaluation_key TEXT NOT NULL,
                    request_json TEXT,
                    candidate_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    attempt_json TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL DEFAULT 0 CHECK(cache_hit IN (0,1)),
                    completed_at TEXT NOT NULL,
                    PRIMARY KEY(sample_key,ordinal),
                    UNIQUE(sample_key,evaluation_key)
                );
                CREATE INDEX IF NOT EXISTS attempts_evaluation ON attempts(evaluation_key);
                CREATE TABLE IF NOT EXISTS edges(
                    edge_id TEXT PRIMARY KEY,
                    child_sample_key TEXT NOT NULL UNIQUE REFERENCES samples(sample_key) ON DELETE CASCADE,
                    edge_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('planned','ready')),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ingest_state(
                    object_kind TEXT NOT NULL,
                    object_key TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','ingested','failed')),
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(object_kind,object_key)
                );
                CREATE TABLE IF NOT EXISTS multidimensional_slices(
                    slice_key TEXT PRIMARY KEY,
                    pair_key TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('active','complete','budget_exhausted','blocked')),
                    new_coordinate_count INTEGER NOT NULL DEFAULT 0 CHECK(new_coordinate_count >= 0),
                    promotion_count INTEGER NOT NULL DEFAULT 0 CHECK(promotion_count >= 0),
                    probe_cursor INTEGER NOT NULL DEFAULT 1 CHECK(probe_cursor >= 1),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS multidimensional_cells(
                    cell_id TEXT PRIMARY KEY,
                    slice_key TEXT NOT NULL REFERENCES multidimensional_slices(slice_key) ON DELETE CASCADE,
                    parent_cell_id TEXT REFERENCES multidimensional_cells(cell_id),
                    level INTEGER NOT NULL CHECK(level >= 0),
                    state TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
                    cell_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS multidimensional_cells_active
                    ON multidimensional_cells(slice_key,active,state,level,cell_id);
                CREATE TABLE IF NOT EXISTS multidimensional_tasks(
                    task_id TEXT PRIMARY KEY,
                    sample_key TEXT NOT NULL REFERENCES samples(sample_key) ON DELETE CASCADE,
                    epoch INTEGER NOT NULL REFERENCES epochs(epoch),
                    sequence INTEGER NOT NULL CHECK(sequence >= 0),
                    purpose TEXT NOT NULL,
                    profile TEXT NOT NULL CHECK(profile IN ('discovery','global_discovery','confirmation')),
                    creates_sample INTEGER NOT NULL CHECK(creates_sample IN (0,1)),
                    state TEXT NOT NULL CHECK(state IN ('planned','running','completed')),
                    task_json TEXT NOT NULL,
                    prepared_case_json TEXT,
                    outcome_json TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    UNIQUE(epoch,sequence)
                );
                CREATE INDEX IF NOT EXISTS multidimensional_tasks_epoch
                    ON multidimensional_tasks(epoch,state,sequence,task_id);
                CREATE TABLE IF NOT EXISTS multidimensional_probes(
                    probe_id TEXT PRIMARY KEY,
                    scope_key TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('pair_global','high_dimensional_global')),
                    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
                    sample_key TEXT REFERENCES samples(sample_key),
                    state TEXT NOT NULL CHECK(state IN ('pending','planned','completed','collision')),
                    definition_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope_key,kind,ordinal)
                );
                CREATE TABLE IF NOT EXISTS multidimensional_rays(
                    ray_id TEXT PRIMARY KEY,
                    ordinal INTEGER NOT NULL UNIQUE CHECK(ordinal >= 0),
                    state TEXT NOT NULL,
                    current_feasible_sample_key TEXT NOT NULL REFERENCES samples(sample_key),
                    previous_feasible_sample_key TEXT REFERENCES samples(sample_key),
                    not_found_sample_key TEXT REFERENCES samples(sample_key),
                    successful_scouts INTEGER NOT NULL DEFAULT 0 CHECK(successful_scouts >= 0),
                    refinement_count INTEGER NOT NULL DEFAULT 0 CHECK(refinement_count >= 0),
                    evaluated_target_count INTEGER NOT NULL DEFAULT 0 CHECK(evaluated_target_count >= 0),
                    ray_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS connectivity_edges(
                    edge_id TEXT PRIMARY KEY,
                    edge_json TEXT NOT NULL,
                    accepted INTEGER NOT NULL CHECK(accepted IN (0,1)),
                    evidence_json TEXT NOT NULL,
                    source_revision INTEGER NOT NULL CHECK(source_revision >= 0),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS family_run_branches(
                    branch_id TEXT PRIMARY KEY,
                    branch_json TEXT NOT NULL,
                    active_sample_count INTEGER NOT NULL DEFAULT 0 CHECK(active_sample_count >= 0),
                    superseded_by TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coverage_snapshots(
                    product_key TEXT NOT NULL,
                    sample_revision INTEGER NOT NULL,
                    connectivity_revision INTEGER NOT NULL,
                    coverage_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(product_key,sample_revision,connectivity_revision)
                );
                CREATE TABLE IF NOT EXISTS boundary_products(
                    product_key TEXT NOT NULL,
                    product_revision INTEGER NOT NULL CHECK(product_revision >= 1),
                    sample_revision INTEGER NOT NULL,
                    connectivity_revision INTEGER NOT NULL,
                    config_hash TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    artifact_path TEXT NOT NULL,
                    product_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(product_key,product_revision),
                    UNIQUE(product_key,sample_revision,connectivity_revision,config_hash)
                );
                CREATE TABLE IF NOT EXISTS refinement_commands(
                    refinement_id TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'pending','running','completed','failed','cancelled','no_op'
                    )),
                    epoch INTEGER REFERENCES epochs(epoch),
                    result_json TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            attempt_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(attempts)").fetchall()
            }
            if "rescue_stage_ordinal" not in attempt_columns:
                connection.execute(
                    "ALTER TABLE attempts ADD COLUMN rescue_stage_ordinal INTEGER"
                )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(FAMILY_RUN_SCHEMA),),
            )
            connection.execute(
                "UPDATE metadata SET value=? WHERE key='schema_version'",
                (str(FAMILY_RUN_SCHEMA),),
            )
            migrated_sample_revision = 0
            if existing == 1:
                migrated_sample_revision = int(connection.execute(
                    """SELECT COUNT(*) FROM samples
                       WHERE state='completed' AND outcome_json IS NOT NULL"""
                ).fetchone()[0])
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('sample_set_revision',?)",
                (str(migrated_sample_revision),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('connectivity_revision','0')"
            )

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

    @staticmethod
    def _revision(connection: sqlite3.Connection, key: str) -> int:
        row = connection.execute(
            "SELECT value FROM metadata WHERE key=?", (key,)
        ).fetchone()
        return 0 if row is None else int(json.loads(row["value"]))

    @classmethod
    def _bump_revision(cls, connection: sqlite3.Connection, key: str) -> int:
        revision = cls._revision(connection, key) + 1
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
            (key, _json(revision)),
        )
        return revision

    @property
    def sample_set_revision(self) -> int:
        return int(self.get_metadata("sample_set_revision", 0))

    @property
    def connectivity_revision(self) -> int:
        return int(self.get_metadata("connectivity_revision", 0))

    def set_metadata(self, key: str, value: Any) -> None:
        with _connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES(?,?)",
                (key, _json(value)),
            )

    def get_metadata(self, key: str, default: Any = None) -> Any:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key=?", (key,)
            ).fetchone()
        return default if row is None else json.loads(row["value"])

    @property
    def initialized(self) -> bool:
        return self.get_metadata("configuration_identity") is not None

    def initialize(
        self,
        *,
        resolved_document: Mapping[str, Any],
        configuration_identity: str,
        source_manifest: Mapping[str, Any],
        anchor_sample: FamilySample,
        anchor_attempt: FamilyAttempt,
        anchor_candidate: CandidateRecord,
        anchor_result: EvaluationResult,
        chains: Sequence[ChainState],
    ) -> None:
        if anchor_sample.classification is not SampleClassification.FEASIBLE_FOUND:
            raise ValueError("family anchor sample must be feasible")
        with self.transaction() as connection:
            prior = connection.execute(
                "SELECT value FROM metadata WHERE key='configuration_identity'"
            ).fetchone()
            if prior is not None:
                if json.loads(prior["value"]) != configuration_identity:
                    raise ValueError("run directory contains a different family configuration")
                return
            for key, value in (
                ("configuration_identity", configuration_identity),
                ("source_manifest", dict(source_manifest)),
                ("resolved_family", dict(resolved_document)),
            ):
                connection.execute(
                    "INSERT INTO metadata(key,value) VALUES(?,?)", (key, _json(value))
                )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('sample_set_revision',?)",
                (_json(1),),
            )
            now = utc_now()
            connection.execute(
                """INSERT INTO epochs(
                       epoch,state,plan_hash,planned_count,completed_count,
                       created_at,started_at,closed_at
                   ) VALUES(0,'closed',?,1,1,?,?,?)""",
                (
                    content_hash(anchor_sample.to_dict(), prefix="emtg-family-anchor-plan-v1"),
                    now, now, now,
                ),
            )
            connection.execute(
                """INSERT INTO samples(
                       sample_key,chain_id,epoch,chain_sequence,purpose,grid_index,state,
                       parent_sample_key,sample_json,task_json,outcome_json,
                       created_at,started_at,completed_at
                   ) VALUES(?,NULL,0,0,'anchor',NULL,'completed',NULL,?,?,?,?,?,?)""",
                (
                    anchor_sample.sample_key,
                    _json(anchor_sample.to_dict()),
                    _json({"kind": "anchor"}),
                    _json(anchor_sample.to_dict()),
                    now, now, now,
                ),
            )
            connection.execute(
                """INSERT INTO attempts(
                       sample_key,ordinal,evaluation_key,request_json,candidate_json,
                       result_json,attempt_json,cache_hit,completed_at
                   ) VALUES(?,?,?,NULL,?,?,?,?,?)""",
                (
                    anchor_sample.sample_key, anchor_attempt.ordinal,
                    anchor_attempt.evaluation_key, _json(candidate_to_dict(anchor_candidate)),
                    _json(result_to_dict(anchor_result)), _json(anchor_attempt.to_dict()), 1, now,
                ),
            )
            for chain in chains:
                self._insert_chain(connection, chain, now)

    @staticmethod
    def _insert_chain(
        connection: sqlite3.Connection, chain: ChainState, now: str | None = None
    ) -> None:
        connection.execute(
            """INSERT INTO chains(
                   chain_id,axis_ordinal,axis_key,direction,rank,state,
                   current_feasible_sample_key,previous_feasible_sample_key,
                   not_found_sample_key,successful_scouts,refinement_count,
                   terminal_reason,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                chain.chain_id, chain.axis_ordinal, chain.axis_key, chain.direction,
                chain.rank, chain.state, chain.current_feasible_sample_key,
                chain.previous_feasible_sample_key, chain.not_found_sample_key,
                chain.successful_scouts, chain.refinement_count, chain.terminal_reason,
                now or utc_now(),
            ),
        )

    @staticmethod
    def _update_chain(connection: sqlite3.Connection, chain: ChainState) -> None:
        connection.execute(
            """UPDATE chains SET state=?,current_feasible_sample_key=?,
                   previous_feasible_sample_key=?,not_found_sample_key=?,
                   successful_scouts=?,refinement_count=?,terminal_reason=?,updated_at=?
               WHERE chain_id=?""",
            (
                chain.state, chain.current_feasible_sample_key,
                chain.previous_feasible_sample_key, chain.not_found_sample_key,
                chain.successful_scouts, chain.refinement_count, chain.terminal_reason,
                utc_now(), chain.chain_id,
            ),
        )

    def chains(self) -> tuple[ChainState, ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute("SELECT * FROM chains ORDER BY rank").fetchall()
        return tuple(ChainState.from_row(row) for row in rows)

    def sample(self, sample_key: str) -> FamilySample:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT sample_json,outcome_json FROM samples WHERE sample_key=?",
                (sample_key,),
            ).fetchone()
        if row is None:
            raise KeyError(sample_key)
        return FamilySample.from_dict(json.loads(row["outcome_json"] or row["sample_json"]))

    def sample_state(self, sample_key: str) -> str:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT state FROM samples WHERE sample_key=?", (sample_key,)
            ).fetchone()
        if row is None:
            raise KeyError(sample_key)
        return str(row["state"])

    def samples(self, *, completed_only: bool = False) -> tuple[FamilySample, ...]:
        clause = " WHERE state='completed'" if completed_only else ""
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT sample_json,outcome_json FROM samples" + clause
                + " ORDER BY epoch,chain_sequence,sample_key"
            ).fetchall()
        return tuple(
            FamilySample.from_dict(json.loads(row["outcome_json"] or row["sample_json"]))
            for row in rows
        )

    def completed_feasible_sample_keys(self, *, before_epoch: int | None = None) -> tuple[str, ...]:
        parameters: list[Any] = []
        epoch_clause = ""
        if before_epoch is not None:
            epoch_clause = " AND epoch<?"
            parameters.append(before_epoch)
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT sample_key,outcome_json FROM samples
                   WHERE state='completed' AND outcome_json IS NOT NULL"""
                + epoch_clause + " ORDER BY epoch,chain_sequence,sample_key",
                tuple(parameters),
            ).fetchall()
        output = []
        for row in rows:
            sample = FamilySample.from_dict(json.loads(row["outcome_json"]))
            if sample.classification is SampleClassification.FEASIBLE_FOUND:
                output.append(str(row["sample_key"]))
        return tuple(output)

    def plan_epoch(self, epoch: int, tasks: Sequence[StoredTask], edges: Sequence[ContinuationEdge]) -> str:
        if not tasks:
            raise ValueError("cannot persist an empty planning epoch")
        ordered = sorted(tasks, key=lambda item: (item.chain_sequence, item.sample_key))
        edge_by_child = {edge.child_sample_key: edge for edge in edges}
        task_keys = {task.sample_key for task in ordered}
        if len(task_keys) != len(ordered):
            raise ValueError("planning epoch contains duplicate sample keys")
        if len(edge_by_child) != len(edges) or set(edge_by_child) != task_keys:
            raise ValueError("planning epoch must contain exactly one edge per sample")
        if len({task.chain_sequence for task in ordered}) != len(ordered):
            raise ValueError("planning epoch contains duplicate chain sequence values")
        plan_document = [
            {
                "sample": task.sample.to_dict(),
                "chain_id": task.chain_id,
                "purpose": task.purpose,
                "grid_index": task.grid_index,
                "task": dict(task.task),
                "edge": edge_by_child[task.sample_key].to_dict(),
            }
            for task in ordered
        ]
        plan_hash = content_hash(plan_document, prefix="emtg-family-epoch-plan-v1")
        now = utc_now()
        with self.transaction() as connection:
            open_row = connection.execute(
                "SELECT epoch FROM epochs WHERE state!='closed'"
            ).fetchone()
            if open_row is not None:
                raise ValueError(f"epoch {open_row['epoch']} is still open")
            connection.execute(
                """INSERT INTO epochs(
                       epoch,state,plan_hash,planned_count,completed_count,created_at
                   ) VALUES(?,'planned',?,?,0,?)""",
                (epoch, plan_hash, len(ordered), now),
            )
            for task in ordered:
                connection.execute(
                    """INSERT INTO samples(
                           sample_key,chain_id,epoch,chain_sequence,purpose,grid_index,
                           state,parent_sample_key,sample_json,task_json,created_at
                       ) VALUES(?,?,?,?,?,?,'planned',?,?,?,?)""",
                    (
                        task.sample_key, task.chain_id, epoch, task.chain_sequence,
                        task.purpose, task.grid_index, task.sample.parent_sample_key,
                        _json(task.sample.to_dict()), _json(dict(task.task)), now,
                    ),
                )
                edge = edge_by_child[task.sample_key]
                connection.execute(
                    """INSERT INTO edges(edge_id,child_sample_key,edge_json,state,updated_at)
                       VALUES(?,?,?,'planned',?)""",
                    (edge.edge_id, task.sample_key, _json(edge.to_dict()), now),
                )
        return plan_hash

    def open_epoch(self) -> int | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT epoch FROM epochs WHERE state!='closed' ORDER BY epoch LIMIT 1"
            ).fetchone()
        return None if row is None else int(row["epoch"])

    def last_closed_epoch(self) -> int:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(epoch),0) AS value FROM epochs WHERE state='closed'"
            ).fetchone()
        return int(row["value"])

    def mirror_refinement(
        self,
        refinement_id: str,
        request: Mapping[str, Any],
        *,
        status: str = "pending",
    ) -> dict[str, Any]:
        """Durably mirror an additive caller refinement command.

        This table is orchestration state only: it never participates in family,
        sample, attempt, or evaluation identity.
        """
        document = _json(dict(request))
        now = utc_now()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM refinement_commands WHERE refinement_id=?",
                (refinement_id,),
            ).fetchone()
            if existing is not None:
                if existing["request_json"] != document:
                    raise ValueError(f"immutable refinement command conflict: {refinement_id}")
            else:
                connection.execute(
                    """INSERT INTO refinement_commands(
                           refinement_id,request_json,status,created_at,updated_at
                       ) VALUES(?,?,?,?,?)""",
                    (refinement_id, document, status, now, now),
                )
        return self.refinement_command(refinement_id)

    def refinement_command(self, refinement_id: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM refinement_commands WHERE refinement_id=?",
                (refinement_id,),
            ).fetchone()
        if row is None:
            raise KeyError(refinement_id)
        return {
            **dict(row),
            "request": json.loads(row["request_json"]),
            "result": None if row["result_json"] is None else json.loads(row["result_json"]),
        }

    def bind_refinement_epoch(self, refinement_id: str, epoch: int) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT status,epoch FROM refinement_commands WHERE refinement_id=?",
                (refinement_id,),
            ).fetchone()
            if row is None:
                raise KeyError(refinement_id)
            if row["epoch"] is not None and int(row["epoch"]) != epoch:
                raise ValueError(f"refinement {refinement_id} is already bound to another epoch")
            connection.execute(
                """UPDATE refinement_commands SET status='running',epoch=?,updated_at=?
                   WHERE refinement_id=?""",
                (epoch, utc_now(), refinement_id),
            )

    def finish_refinement(
        self,
        refinement_id: str,
        status: str,
        *,
        result: Mapping[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if status not in {"completed", "failed", "cancelled", "no_op"}:
            raise ValueError("invalid terminal refinement status")
        with self.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM refinement_commands WHERE refinement_id=?",
                (refinement_id,),
            ).fetchone() is None:
                raise KeyError(refinement_id)
            connection.execute(
                """UPDATE refinement_commands
                   SET status=?,result_json=?,error=?,updated_at=?
                   WHERE refinement_id=?""",
                (
                    status,
                    None if result is None else _json(dict(result)),
                    error,
                    utc_now(),
                    refinement_id,
                ),
            )

    def tasks_for_epoch(self, epoch: int, *, states: Sequence[str] = ("planned", "running")) -> tuple[StoredTask, ...]:
        placeholders = ",".join("?" for _ in states)
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                f"""SELECT * FROM samples WHERE epoch=? AND state IN ({placeholders})
                    ORDER BY chain_sequence,sample_key""",
                (epoch, *states),
            ).fetchall()
        return tuple(
            StoredTask(
                FamilySample.from_dict(json.loads(row["sample_json"])),
                str(row["chain_id"]), int(row["epoch"]), int(row["chain_sequence"]),
                str(row["purpose"]), int(row["grid_index"]),
                json.loads(row["task_json"]), str(row["state"]),
                None if row["prepared_case_json"] is None else json.loads(row["prepared_case_json"]),
            )
            for row in rows
        )

    def reset_running(self) -> int:
        with _connect(self.database_path) as connection:
            count = connection.execute(
                "UPDATE samples SET state='planned',started_at=NULL WHERE state='running'"
            ).rowcount
            connection.execute(
                "UPDATE epochs SET state='planned',started_at=NULL WHERE state='running'"
            )
        return int(count)

    def mark_running(self, sample_key: str, prepared_case: Mapping[str, Any] | None = None) -> None:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT epoch,state FROM samples WHERE sample_key=?", (sample_key,)
            ).fetchone()
            if row is None:
                raise KeyError(sample_key)
            if row["state"] == "completed":
                return
            connection.execute(
                """UPDATE samples SET state='running',started_at=COALESCE(started_at,?),
                       prepared_case_json=COALESCE(?,prepared_case_json)
                   WHERE sample_key=?""",
                (now, None if prepared_case is None else _json(dict(prepared_case)), sample_key),
            )
            connection.execute(
                """UPDATE epochs SET state='running',started_at=COALESCE(started_at,?)
                   WHERE epoch=? AND state!='closed'""",
                (now, int(row["epoch"])),
            )

    def record_attempt(
        self,
        *,
        sample_key: str,
        attempt: FamilyAttempt,
        candidate: CandidateRecord,
        result: EvaluationResult,
        request: Mapping[str, Any] | None,
        cache_hit: bool,
    ) -> None:
        if attempt.sample_key != sample_key:
            raise ValueError("family attempt belongs to another sample")
        if attempt.evaluation_key != result.evaluation_key:
            raise ValueError("family attempt and result evaluation keys differ")
        if attempt.candidate_id != result.candidate_id:
            raise ValueError("family attempt and result candidate IDs differ")
        if candidate.candidate_id != result.candidate_id:
            raise ValueError("family result belongs to another candidate")
        document = _json(attempt.to_dict())
        result_document = _json(result_to_dict(result))
        candidate_document = _json(candidate_to_dict(candidate))
        request_document = None if request is None else _json(dict(request))
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM attempts WHERE sample_key=? AND ordinal=?",
                (sample_key, attempt.ordinal),
            ).fetchone()
            if existing is not None:
                comparable = (
                    existing["evaluation_key"], existing["request_json"],
                    existing["candidate_json"], existing["result_json"], existing["attempt_json"],
                )
                requested = (
                    attempt.evaluation_key, request_document, candidate_document,
                    result_document, document,
                )
                if comparable != requested:
                    raise ValueError(
                        f"immutable family attempt conflict for {sample_key} ordinal {attempt.ordinal}"
                    )
                return
            connection.execute(
                """INSERT INTO attempts(
                       sample_key,ordinal,rescue_stage_ordinal,evaluation_key,request_json,candidate_json,
                       result_json,attempt_json,cache_hit,completed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    sample_key, attempt.ordinal, attempt.rescue_stage_ordinal,
                    attempt.evaluation_key, request_document,
                    candidate_document, result_document, document, 1 if cache_hit else 0,
                    utc_now(),
                ),
            )

    def complete_sample(
        self,
        sample: FamilySample,
        edge: ContinuationEdge,
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT epoch,state,outcome_json FROM samples WHERE sample_key=?",
                (sample.sample_key,),
            ).fetchone()
            if row is None:
                raise KeyError(sample.sample_key)
            if sample.classification is SampleClassification.FEASIBLE_FOUND and (
                sample.best_evaluation_key is None
            ):
                raise ValueError("feasible family sample has no best evaluation")
            if sample.best_evaluation_key is not None:
                best = connection.execute(
                    """SELECT 1 FROM attempts
                       WHERE sample_key=? AND evaluation_key=?""",
                    (sample.sample_key, sample.best_evaluation_key),
                ).fetchone()
                if best is None:
                    raise ValueError(
                        "family sample best evaluation was not persisted as an attempt"
                    )
            outcome_document = _json(sample.to_dict())
            if row["state"] == "completed":
                if row["outcome_json"] != outcome_document:
                    raise ValueError(f"immutable completed sample conflict for {sample.sample_key}")
                return
            connection.execute(
                """UPDATE samples SET state='completed',outcome_json=?,completed_at=?
                   WHERE sample_key=?""",
                (outcome_document, now, sample.sample_key),
            )
            connection.execute(
                """UPDATE epochs SET completed_count=completed_count+1 WHERE epoch=?""",
                (int(row["epoch"]),),
            )
            connection.execute(
                """UPDATE edges SET edge_json=?,state='ready',updated_at=?
                   WHERE child_sample_key=?""",
                (_json(edge.to_dict()), now, sample.sample_key),
            )
            self._bump_revision(connection, "sample_set_revision")

    def epoch_complete(self, epoch: int) -> bool:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT planned_count,completed_count FROM epochs WHERE epoch=?", (epoch,)
            ).fetchone()
        if row is None:
            raise KeyError(epoch)
        return int(row["planned_count"]) == int(row["completed_count"])

    def close_epoch(self, epoch: int, chains: Sequence[ChainState]) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT planned_count,completed_count,state FROM epochs WHERE epoch=?", (epoch,)
            ).fetchone()
            if row is None:
                raise KeyError(epoch)
            if int(row["planned_count"]) != int(row["completed_count"]):
                raise ValueError(f"epoch {epoch} still has incomplete work")
            for chain in chains:
                self._update_chain(connection, chain)
            connection.execute(
                "UPDATE epochs SET state='closed',closed_at=? WHERE epoch=?",
                (utc_now(), epoch),
            )

    def attempts(self, sample_key: str | None = None) -> tuple[dict[str, Any], ...]:
        clause = " WHERE sample_key=?" if sample_key is not None else ""
        parameters = () if sample_key is None else (sample_key,)
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM attempts" + clause + " ORDER BY sample_key,ordinal",
                parameters,
            ).fetchall()
        return tuple(
            {
                **dict(row),
                "request": None if row["request_json"] is None else json.loads(row["request_json"]),
                "candidate": candidate_from_dict(json.loads(row["candidate_json"])),
                "result": result_from_dict(json.loads(row["result_json"])),
                "attempt": FamilyAttempt.from_dict(json.loads(row["attempt_json"])),
                "cache_hit": bool(row["cache_hit"]),
            }
            for row in rows
        )

    def sample_resolved_entirely_from_cache(self, sample_key: str) -> bool:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT COUNT(*) AS count,COALESCE(MIN(cache_hit),0) AS all_hits
                   FROM attempts WHERE sample_key=?""",
                (sample_key,),
            ).fetchone()
        return int(row["count"]) > 0 and bool(row["all_hits"])

    def best_result(self, sample_key: str) -> EvaluationResult:
        sample = self.sample(sample_key)
        if sample.best_evaluation_key is None:
            raise KeyError(f"sample {sample_key} has no best evaluation")
        with _connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT result_json FROM attempts
                   WHERE sample_key=? AND evaluation_key=?""",
                (sample_key, sample.best_evaluation_key),
            ).fetchone()
        if row is None:
            raise KeyError(sample.best_evaluation_key)
        return result_from_dict(json.loads(row["result_json"]))

    def edge_for_child(self, sample_key: str) -> ContinuationEdge:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT edge_json FROM edges WHERE child_sample_key=?", (sample_key,)
            ).fetchone()
        if row is None:
            raise KeyError(sample_key)
        return ContinuationEdge.from_dict(json.loads(row["edge_json"]))

    def ready_edges(self) -> tuple[ContinuationEdge, ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT edge_json FROM edges WHERE state='ready' ORDER BY edge_id"
            ).fetchall()
            connectivity = connection.execute(
                "SELECT edge_json FROM connectivity_edges ORDER BY edge_id"
            ).fetchall()
        by_id = {
            edge.edge_id: edge
            for edge in (
                ContinuationEdge.from_dict(json.loads(row["edge_json"]))
                for row in (*rows, *connectivity)
            )
        }
        return tuple(by_id[key] for key in sorted(by_id))

    def initialize_multidimensional(
        self,
        slices: Sequence[PairSliceDefinition],
        cells: Sequence[QuadtreeCell],
    ) -> None:
        now = utc_now()
        by_slice = {item.slice_key: item for item in slices}
        if any(cell.slice_key not in by_slice for cell in cells):
            raise ValueError("quadtree cell belongs to an unknown pair slice")
        with self.transaction() as connection:
            for item in slices:
                document = _json(item.to_dict())
                existing = connection.execute(
                    "SELECT definition_json FROM multidimensional_slices WHERE slice_key=?",
                    (item.slice_key,),
                ).fetchone()
                if existing is not None and existing["definition_json"] != document:
                    raise ValueError(f"immutable pair-slice conflict for {item.slice_key}")
                connection.execute(
                    """INSERT OR IGNORE INTO multidimensional_slices(
                           slice_key,pair_key,definition_json,state,updated_at
                       ) VALUES(?,?,?,'active',?)""",
                    (item.slice_key, item.pair_key, document, now),
                )
            for cell in cells:
                self._upsert_cell(connection, cell, active=True, now=now)
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('multidimensional_initialized',?)",
                (_json(True),),
            )

    @staticmethod
    def _upsert_cell(
        connection: sqlite3.Connection,
        cell: QuadtreeCell,
        *,
        active: bool,
        now: str | None = None,
    ) -> None:
        document = _json(cell.to_dict())
        existing = connection.execute(
            "SELECT slice_key,parent_cell_id,level FROM multidimensional_cells WHERE cell_id=?",
            (cell.cell_id,),
        ).fetchone()
        if existing is not None and (
            existing["slice_key"] != cell.slice_key
            or existing["parent_cell_id"] != cell.parent_cell_id
            or int(existing["level"]) != cell.level
        ):
            raise ValueError(f"immutable quadtree-cell conflict for {cell.cell_id}")
        connection.execute(
            """INSERT INTO multidimensional_cells(
                   cell_id,slice_key,parent_cell_id,level,state,active,cell_json,updated_at
               ) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(cell_id) DO UPDATE SET
                   state=excluded.state,active=excluded.active,
                   cell_json=excluded.cell_json,updated_at=excluded.updated_at""",
            (
                cell.cell_id, cell.slice_key, cell.parent_cell_id, cell.level,
                cell.state.value, 1 if active else 0, document, now or utc_now(),
            ),
        )

    @property
    def multidimensional_initialized(self) -> bool:
        return bool(self.get_metadata("multidimensional_initialized", False))

    def multidimensional_slices(self) -> tuple[dict[str, Any], ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM multidimensional_slices ORDER BY slice_key"
            ).fetchall()
        return tuple({
            **dict(row),
            "definition": PairSliceDefinition.from_dict(json.loads(row["definition_json"])),
        } for row in rows)

    def multidimensional_cells(
        self, slice_key: str | None = None, *, active_only: bool = False,
    ) -> tuple[QuadtreeCell, ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if slice_key is not None:
            clauses.append("slice_key=?")
            parameters.append(slice_key)
        if active_only:
            clauses.append("active=1")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT cell_json FROM multidimensional_cells" + where
                + " ORDER BY slice_key,level,cell_id",
                tuple(parameters),
            ).fetchall()
        return tuple(QuadtreeCell.from_dict(json.loads(row["cell_json"])) for row in rows)

    def replace_cell_with_children(
        self, parent: QuadtreeCell, children: Sequence[QuadtreeCell],
    ) -> None:
        if any(child.parent_cell_id != parent.cell_id for child in children):
            raise ValueError("quadtree children do not reference their parent")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT active FROM multidimensional_cells WHERE cell_id=?",
                (parent.cell_id,),
            ).fetchone()
            if row is None:
                raise KeyError(parent.cell_id)
            connection.execute(
                "UPDATE multidimensional_cells SET active=0,updated_at=? WHERE cell_id=?",
                (utc_now(), parent.cell_id),
            )
            for child in children:
                self._upsert_cell(connection, child, active=True)

    def update_cell(self, cell: QuadtreeCell, *, active: bool = True) -> None:
        with self.transaction() as connection:
            self._upsert_cell(connection, cell, active=active)

    def update_slice_state(
        self,
        slice_key: str,
        *,
        state: str | None = None,
        coordinate_increment: int = 0,
        promotion_increment: int = 0,
        probe_cursor: int | None = None,
    ) -> None:
        if coordinate_increment < 0 or promotion_increment < 0:
            raise ValueError("slice counters cannot decrease")
        assignments = [
            "new_coordinate_count=new_coordinate_count+?",
            "promotion_count=promotion_count+?",
            "updated_at=?",
        ]
        parameters: list[Any] = [coordinate_increment, promotion_increment, utc_now()]
        if state is not None:
            assignments.append("state=?")
            parameters.append(state)
        if probe_cursor is not None:
            assignments.append("probe_cursor=?")
            parameters.append(probe_cursor)
        parameters.append(slice_key)
        with _connect(self.database_path) as connection:
            changed = connection.execute(
                f"UPDATE multidimensional_slices SET {','.join(assignments)} WHERE slice_key=?",
                tuple(parameters),
            ).rowcount
        if not changed:
            raise KeyError(slice_key)

    def initialize_rays(self, rays: Sequence[RayState]) -> None:
        now = utc_now()
        with self.transaction() as connection:
            for ray in rays:
                document = _json(ray.to_dict())
                existing = connection.execute(
                    "SELECT ray_json FROM multidimensional_rays WHERE ray_id=?", (ray.ray_id,)
                ).fetchone()
                if existing is not None and existing["ray_json"] != document:
                    raise ValueError(f"immutable initial ray conflict for {ray.ray_id}")
                connection.execute(
                    """INSERT OR IGNORE INTO multidimensional_rays(
                           ray_id,ordinal,state,current_feasible_sample_key,
                           previous_feasible_sample_key,not_found_sample_key,
                           successful_scouts,refinement_count,evaluated_target_count,
                           ray_json,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ray.ray_id, ray.ordinal, ray.state, ray.current_feasible_sample_key,
                        ray.previous_feasible_sample_key, ray.not_found_sample_key,
                        ray.successful_scouts, ray.refinement_count,
                        ray.evaluated_target_count, document, now,
                    ),
                )

    def rays(self) -> tuple[RayState, ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT ray_json FROM multidimensional_rays ORDER BY ordinal"
            ).fetchall()
        return tuple(RayState.from_dict(json.loads(row["ray_json"])) for row in rows)

    def update_ray(self, ray: RayState) -> None:
        with _connect(self.database_path) as connection:
            changed = connection.execute(
                """UPDATE multidimensional_rays SET state=?,current_feasible_sample_key=?,
                       previous_feasible_sample_key=?,not_found_sample_key=?,
                       successful_scouts=?,refinement_count=?,evaluated_target_count=?,
                       ray_json=?,updated_at=? WHERE ray_id=?""",
                (
                    ray.state, ray.current_feasible_sample_key,
                    ray.previous_feasible_sample_key, ray.not_found_sample_key,
                    ray.successful_scouts, ray.refinement_count,
                    ray.evaluated_target_count, _json(ray.to_dict()), utc_now(), ray.ray_id,
                ),
            ).rowcount
        if not changed:
            raise KeyError(ray.ray_id)

    def multidimensional_task_rows(self) -> tuple[dict[str, Any], ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM multidimensional_tasks ORDER BY epoch,sequence,task_id"
            ).fetchall()
        return tuple({
            **dict(row),
            "task": json.loads(row["task_json"]),
            "outcome": None if row["outcome_json"] is None else json.loads(row["outcome_json"]),
        } for row in rows)

    def save_probe(
        self,
        *,
        probe_id: str,
        scope_key: str,
        kind: str,
        ordinal: int,
        sample_key: str | None,
        state: str,
        definition: Mapping[str, Any],
    ) -> None:
        if state not in {"pending", "planned", "completed", "collision"}:
            raise ValueError(f"unsupported probe state {state!r}")
        document = _json(dict(definition))
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM multidimensional_probes WHERE probe_id=?", (probe_id,)
            ).fetchone()
            if existing is not None:
                immutable = (
                    existing["scope_key"], existing["kind"], int(existing["ordinal"]),
                    existing["sample_key"], existing["definition_json"],
                )
                requested = (scope_key, kind, ordinal, sample_key, document)
                if immutable != requested:
                    raise ValueError(f"immutable multidimensional probe conflict for {probe_id}")
            connection.execute(
                """INSERT INTO multidimensional_probes(
                       probe_id,scope_key,kind,ordinal,sample_key,state,definition_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(probe_id) DO UPDATE SET
                       state=excluded.state,updated_at=excluded.updated_at""",
                (probe_id, scope_key, kind, ordinal, sample_key, state, document, utc_now()),
            )

    def probes(
        self, *, scope_key: str | None = None, kind: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if scope_key is not None:
            clauses.append("scope_key=?")
            parameters.append(scope_key)
        if kind is not None:
            clauses.append("kind=?")
            parameters.append(kind)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM multidimensional_probes" + where
                + " ORDER BY scope_key,kind,ordinal",
                tuple(parameters),
            ).fetchall()
        return tuple({**dict(row), "definition": json.loads(row["definition_json"])} for row in rows)

    @staticmethod
    def _multidimensional_task_id(task: StoredTask) -> str:
        explicit = task.task.get("task_id")
        if explicit:
            return str(explicit)
        return content_hash(
            {
                "schema_version": 1,
                "sample_key": task.sample_key,
                "epoch": task.epoch,
                "sequence": task.chain_sequence,
                "purpose": task.task.get("purpose", task.purpose),
                "profile": task.task.get("evaluation_profile", "discovery"),
            },
            prefix="emtg-family-multidimensional-task-v1",
        )

    def plan_multidimensional_epoch(
        self,
        epoch: int,
        tasks: Sequence[StoredTask],
        edges: Sequence[ContinuationEdge] = (),
    ) -> str:
        if not tasks:
            raise ValueError("cannot persist an empty multidimensional epoch")
        ordered = sorted(tasks, key=lambda item: (item.chain_sequence, item.sample_key))
        edge_by_child = {edge.child_sample_key: edge for edge in edges}
        identifiers = [self._multidimensional_task_id(task) for task in ordered]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("multidimensional epoch contains duplicate task IDs")
        plan_document = []
        for task, task_id in zip(ordered, identifiers):
            creates = bool(task.task.get("creates_sample", True))
            edge = edge_by_child.get(task.sample_key)
            if creates and edge is None:
                raise ValueError("new multidimensional samples require a continuation edge")
            if not creates and edge is not None:
                raise ValueError("confirmation tasks cannot create continuation edges")
            plan_document.append({
                "task_id": task_id,
                "sample": task.sample.to_dict(),
                "purpose": task.task.get("purpose", task.purpose),
                "profile": task.task.get("evaluation_profile", "discovery"),
                "creates_sample": creates,
                "task": dict(task.task),
                "edge": None if edge is None else edge.to_dict(),
            })
        plan_hash = content_hash(plan_document, prefix="emtg-family-multidimensional-epoch-v1")
        now = utc_now()
        with self.transaction() as connection:
            open_row = connection.execute(
                "SELECT epoch FROM epochs WHERE state!='closed'"
            ).fetchone()
            if open_row is not None:
                raise ValueError(f"epoch {open_row['epoch']} is still open")
            connection.execute(
                """INSERT INTO epochs(
                       epoch,state,plan_hash,planned_count,completed_count,created_at
                   ) VALUES(?,'planned',?,?,0,?)""",
                (epoch, plan_hash, len(ordered), now),
            )
            for task, task_id in zip(ordered, identifiers):
                creates = bool(task.task.get("creates_sample", True))
                profile = str(task.task.get("evaluation_profile", "discovery"))
                purpose = str(task.task.get("purpose", task.purpose))
                existing = connection.execute(
                    "SELECT state FROM samples WHERE sample_key=?", (task.sample_key,)
                ).fetchone()
                if creates:
                    if existing is not None:
                        raise ValueError(f"new multidimensional sample already exists: {task.sample_key}")
                    connection.execute(
                        """INSERT INTO samples(
                               sample_key,chain_id,epoch,chain_sequence,purpose,grid_index,state,
                               parent_sample_key,sample_json,task_json,created_at
                           ) VALUES(?,NULL,?,?, 'refine',?,'planned',?,?,?,?)""",
                        (
                            task.sample_key, epoch, task.chain_sequence, task.grid_index,
                            task.sample.parent_sample_key, _json(task.sample.to_dict()),
                            _json(dict(task.task)), now,
                        ),
                    )
                    edge = edge_by_child[task.sample_key]
                    connection.execute(
                        """INSERT INTO edges(edge_id,child_sample_key,edge_json,state,updated_at)
                           VALUES(?,?,?,'planned',?)""",
                        (edge.edge_id, task.sample_key, _json(edge.to_dict()), now),
                    )
                elif existing is None or existing["state"] != "completed":
                    raise ValueError("confirmation sample is absent or incomplete")
                connection.execute(
                    """INSERT INTO multidimensional_tasks(
                           task_id,sample_key,epoch,sequence,purpose,profile,creates_sample,
                           state,task_json,created_at
                       ) VALUES(?,?,?,?,?,?,?,'planned',?,?)""",
                    (
                        task_id, task.sample_key, epoch, task.chain_sequence, purpose, profile,
                        1 if creates else 0, _json(dict(task.task)), now,
                    ),
                )
        return plan_hash

    def multidimensional_tasks_for_epoch(
        self, epoch: int, *, states: Sequence[str] = ("planned", "running"),
    ) -> tuple[StoredTask, ...]:
        placeholders = ",".join("?" for _ in states)
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                f"""SELECT t.*,s.sample_json,s.outcome_json
                    FROM multidimensional_tasks t JOIN samples s ON s.sample_key=t.sample_key
                    WHERE t.epoch=? AND t.state IN ({placeholders})
                    ORDER BY t.sequence,t.task_id""",
                (epoch, *states),
            ).fetchall()
        return tuple(
            StoredTask(
                FamilySample.from_dict(json.loads(row["outcome_json"] or row["sample_json"])),
                "", int(row["epoch"]), int(row["sequence"]), str(row["purpose"]),
                int(json.loads(row["task_json"]).get("grid_index", row["sequence"])),
                json.loads(row["task_json"]), str(row["state"]),
                None if row["prepared_case_json"] is None else json.loads(row["prepared_case_json"]),
            )
            for row in rows
        )

    def mark_multidimensional_running(
        self, task: StoredTask, prepared_case: Mapping[str, Any] | None = None,
    ) -> None:
        task_id = self._multidimensional_task_id(task)
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT epoch,creates_sample,state FROM multidimensional_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["state"] == "completed":
                return
            connection.execute(
                """UPDATE multidimensional_tasks SET state='running',
                       started_at=COALESCE(started_at,?),
                       prepared_case_json=COALESCE(?,prepared_case_json)
                   WHERE task_id=?""",
                (now, None if prepared_case is None else _json(dict(prepared_case)), task_id),
            )
            if bool(row["creates_sample"]):
                connection.execute(
                    "UPDATE samples SET state='running',started_at=COALESCE(started_at,?) WHERE sample_key=?",
                    (now, task.sample_key),
                )
            connection.execute(
                """UPDATE epochs SET state='running',started_at=COALESCE(started_at,?)
                   WHERE epoch=? AND state!='closed'""",
                (now, int(row["epoch"])),
            )

    def complete_multidimensional_task(
        self,
        task: StoredTask,
        sample: FamilySample,
        edge: ContinuationEdge | None,
    ) -> None:
        task_id = self._multidimensional_task_id(task)
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM multidimensional_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["state"] == "completed":
                existing = json.loads(row["outcome_json"])
                if canonical_json(existing) != canonical_json(sample.to_dict()):
                    raise ValueError(f"immutable multidimensional task conflict for {task_id}")
                return
            if sample.sample_key != row["sample_key"]:
                raise ValueError("multidimensional outcome belongs to another sample")
            if sample.best_evaluation_key is not None:
                best = connection.execute(
                    "SELECT 1 FROM attempts WHERE sample_key=? AND evaluation_key=?",
                    (sample.sample_key, sample.best_evaluation_key),
                ).fetchone()
                if best is None:
                    raise ValueError("multidimensional best evaluation was not persisted")
            current_row = connection.execute(
                "SELECT state,outcome_json FROM samples WHERE sample_key=?", (sample.sample_key,)
            ).fetchone()
            if current_row is None:
                raise KeyError(sample.sample_key)
            truth_changed = bool(row["creates_sample"])
            if bool(row["creates_sample"]):
                if edge is None or edge.child_sample_key != sample.sample_key:
                    raise ValueError("new multidimensional outcome requires its planned edge")
                connection.execute(
                    """UPDATE edges SET edge_json=?,state='ready',updated_at=?
                       WHERE child_sample_key=?""",
                    (_json(edge.to_dict()), now, sample.sample_key),
                )
            else:
                if edge is not None:
                    raise ValueError("confirmation outcome cannot add an edge")
                old = FamilySample.from_dict(json.loads(current_row["outcome_json"]))
                if old.classification is not SampleClassification.UNKNOWN and (
                    sample.classification is not old.classification
                ):
                    raise ValueError("confirmation cannot replace conclusive evaluated truth")
            outcome = _json(sample.to_dict())
            if not bool(row["creates_sample"]):
                truth_changed = current_row["outcome_json"] != outcome
            connection.execute(
                """UPDATE samples SET state='completed',outcome_json=?,completed_at=COALESCE(completed_at,?)
                   WHERE sample_key=?""",
                (outcome, now, sample.sample_key),
            )
            connection.execute(
                """UPDATE multidimensional_tasks SET state='completed',outcome_json=?,completed_at=?
                   WHERE task_id=?""",
                (outcome, now, task_id),
            )
            connection.execute(
                "UPDATE epochs SET completed_count=completed_count+1 WHERE epoch=?",
                (int(row["epoch"]),),
            )
            if truth_changed:
                self._bump_revision(connection, "sample_set_revision")

    def multidimensional_epoch(self, epoch: int) -> bool:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM multidimensional_tasks WHERE epoch=? LIMIT 1", (epoch,)
            ).fetchone()
        return row is not None

    def reset_running(self) -> int:
        with _connect(self.database_path) as connection:
            count = connection.execute(
                "UPDATE samples SET state='planned',started_at=NULL WHERE state='running'"
            ).rowcount
            md_count = connection.execute(
                """UPDATE multidimensional_tasks SET state='planned',started_at=NULL
                   WHERE state='running'"""
            ).rowcount
            connection.execute(
                "UPDATE epochs SET state='planned',started_at=NULL WHERE state='running'"
            )
        return int(count) + int(md_count)

    def save_connectivity(
        self,
        edges: Sequence[tuple[ContinuationEdge, bool, Mapping[str, Any]]],
        assignments: Mapping[str, str],
        branches: Sequence[BranchDefinition],
    ) -> bool:
        now = utc_now()
        changed = False
        with self.transaction() as connection:
            next_revision = self._revision(connection, "connectivity_revision") + 1
            for edge, accepted, evidence in sorted(edges, key=lambda item: item[0].edge_id):
                edge_document = _json(edge.to_dict())
                evidence_document = _json(dict(evidence))
                existing = connection.execute(
                    "SELECT edge_json,accepted,evidence_json FROM connectivity_edges WHERE edge_id=?",
                    (edge.edge_id,),
                ).fetchone()
                payload = (edge_document, 1 if accepted else 0, evidence_document)
                if existing is None or tuple(existing) != payload:
                    changed = True
                    connection.execute(
                        """INSERT INTO connectivity_edges(
                               edge_id,edge_json,accepted,evidence_json,source_revision,updated_at
                           ) VALUES(?,?,?,?,?,?)
                           ON CONFLICT(edge_id) DO UPDATE SET
                               edge_json=excluded.edge_json,accepted=excluded.accepted,
                               evidence_json=excluded.evidence_json,
                               source_revision=excluded.source_revision,updated_at=excluded.updated_at""",
                        (edge.edge_id, *payload, next_revision, now),
                    )
            branch_by_id = {item.branch_id: item for item in branches}
            active_counts: dict[str, int] = {}
            for sample_key, branch_id in sorted(assignments.items()):
                row = connection.execute(
                    "SELECT outcome_json FROM samples WHERE sample_key=?", (sample_key,)
                ).fetchone()
                if row is None or row["outcome_json"] is None:
                    raise KeyError(sample_key)
                sample = FamilySample.from_dict(json.loads(row["outcome_json"]))
                if sample.branch_id != branch_id:
                    sample = replace(sample, branch_id=branch_id)
                    connection.execute(
                        "UPDATE samples SET outcome_json=? WHERE sample_key=?",
                        (_json(sample.to_dict()), sample_key),
                    )
                    changed = True
                active_counts[branch_id] = active_counts.get(branch_id, 0) + 1
            for branch_id, branch in sorted(branch_by_id.items()):
                document = _json(branch.to_dict())
                existing = connection.execute(
                    "SELECT branch_json FROM family_run_branches WHERE branch_id=?", (branch_id,)
                ).fetchone()
                if existing is None:
                    changed = True
                    connection.execute(
                        """INSERT INTO family_run_branches(
                               branch_id,branch_json,active_sample_count,updated_at
                           ) VALUES(?,?,?,?)""",
                        (branch_id, document, active_counts.get(branch_id, 0), now),
                    )
                elif existing["branch_json"] != document:
                    raise ValueError(f"immutable family branch conflict for {branch_id}")
            rows = connection.execute(
                "SELECT branch_id,active_sample_count FROM family_run_branches"
            ).fetchall()
            for row in rows:
                count = active_counts.get(str(row["branch_id"]), 0)
                if int(row["active_sample_count"]) != count:
                    changed = True
                    connection.execute(
                        "UPDATE family_run_branches SET active_sample_count=?,updated_at=? WHERE branch_id=?",
                        (count, now, row["branch_id"]),
                    )
            if changed:
                self._bump_revision(connection, "connectivity_revision")
        return changed

    def branches(self, *, active_only: bool = False) -> tuple[BranchDefinition, ...]:
        clause = " WHERE active_sample_count>0" if active_only else ""
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT branch_json FROM family_run_branches" + clause + " ORDER BY branch_id"
            ).fetchall()
        return tuple(BranchDefinition.from_dict(json.loads(row["branch_json"])) for row in rows)

    def save_coverage(self, coverage: CoverageSnapshot) -> None:
        document = _json(coverage.to_dict())
        with _connect(self.database_path) as connection:
            connection.execute(
                """INSERT OR IGNORE INTO coverage_snapshots(
                       product_key,sample_revision,connectivity_revision,coverage_json,created_at
                   ) VALUES(?,?,?,?,?)""",
                (
                    coverage.product_key, coverage.source_sample_revision,
                    coverage.source_connectivity_revision, document, utc_now(),
                ),
            )

    def next_boundary_product_revision(self, product_key: str) -> int:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(product_revision),0)+1 FROM boundary_products WHERE product_key=?",
                (product_key,),
            ).fetchone()
        return int(row[0])

    def save_boundary_product(self, product: BoundaryProduct) -> dict[str, Any]:
        document = product.to_dict()
        encoded = _json(document)
        with _connect(self.database_path) as connection:
            existing = connection.execute(
                """SELECT product_revision,artifact_path,content_hash FROM boundary_products
                   WHERE product_key=? AND sample_revision=? AND connectivity_revision=?
                     AND config_hash=?""",
                (
                    product.product_key, product.source_sample_revision,
                    product.source_connectivity_revision, product.config_hash,
                ),
            ).fetchone()
        if existing is not None:
            return dict(existing)
        expected = self.next_boundary_product_revision(product.product_key)
        if product.product_revision != expected:
            raise ValueError(
                f"boundary product revision {product.product_revision} should be {expected}"
            )
        directory = self.run_directory / "boundary-products" / product.product_key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"revision-{product.product_revision}.json"
        atomic_write_json(path, document)
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO boundary_products(
                       product_key,product_revision,sample_revision,connectivity_revision,
                       config_hash,content_hash,artifact_path,product_json,created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    product.product_key, product.product_revision,
                    product.source_sample_revision, product.source_connectivity_revision,
                    product.config_hash, product.content_hash, str(path), encoded, utc_now(),
                ),
            )
        self.save_coverage(product.coverage)
        return {
            "product_revision": product.product_revision,
            "artifact_path": str(path),
            "content_hash": product.content_hash,
        }

    def latest_boundary_products(self) -> tuple[dict[str, Any], ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT p.* FROM boundary_products p JOIN (
                       SELECT product_key,MAX(product_revision) AS revision
                       FROM boundary_products GROUP BY product_key
                   ) latest ON latest.product_key=p.product_key
                           AND latest.revision=p.product_revision
                   ORDER BY p.product_key"""
            ).fetchall()
        return tuple({**dict(row), "product": json.loads(row["product_json"])} for row in rows)

    def counts(self) -> dict[str, int]:
        with _connect(self.database_path) as connection:
            sample_rows = connection.execute(
                "SELECT state,COUNT(*) AS count FROM samples GROUP BY state"
            ).fetchall()
            attempt_count = int(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
            cache_hits = int(connection.execute(
                "SELECT COALESCE(SUM(cache_hit),0) FROM attempts"
            ).fetchone()[0])
            classifications = connection.execute(
                "SELECT outcome_json FROM samples WHERE state='completed' AND outcome_json IS NOT NULL"
            ).fetchall()
        output = {"planned": 0, "running": 0, "completed": 0, "attempts": attempt_count,
                  "cache_hits": cache_hits, "feasible_found": 0,
                  "confirmed_not_found": 0, "unknown": 0}
        for row in sample_rows:
            output[str(row["state"])] = int(row["count"])
        for row in classifications:
            classification = FamilySample.from_dict(json.loads(row["outcome_json"])).classification.value
            output[classification] += 1
        return output

    def checkpoint(self, state: Mapping[str, Any]) -> dict[str, Any]:
        revision = int(self.get_metadata("checkpoint_revision", 0)) + 1
        payload = {
            "schema_version": FAMILY_CHECKPOINT_SCHEMA,
            "checkpoint_revision": revision,
            "database": self.database_path.name,
            "run_directory": str(self.run_directory),
            "updated_at": utc_now(),
            **dict(state),
        }
        with self.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('checkpoint_revision',?)",
                (_json(revision),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('checkpoint',?)",
                (_json(payload),),
            )
        atomic_write_json(self.checkpoint_path, payload)
        return payload

    def load_checkpoint(self) -> dict[str, Any] | None:
        database_value = self.get_metadata("checkpoint")
        if database_value is None:
            return None
        if int(database_value.get("schema_version", -1)) != FAMILY_CHECKPOINT_SCHEMA:
            raise ValueError("family checkpoint schema is incompatible")
        file_value = None
        if self.checkpoint_path.is_file():
            try:
                file_value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                file_value = None
        if (
            file_value is None
            or int(file_value.get("checkpoint_revision", -1))
            < int(database_value["checkpoint_revision"])
        ):
            atomic_write_json(self.checkpoint_path, database_value)
        return database_value

    def verify_integrity(self) -> None:
        with _connect(self.database_path) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise ValueError(f"family database integrity check failed: {integrity}")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValueError(f"family database foreign-key check failed: {violations[:3]}")

    def set_ingest_state(
        self, object_kind: str, object_key: str, document: Mapping[str, Any],
        *, status: str, error: str | None = None,
    ) -> None:
        digest = content_hash(document, prefix="emtg-family-ingest-object-v1")
        with _connect(self.database_path) as connection:
            connection.execute(
                """INSERT INTO ingest_state(
                       object_kind,object_key,content_hash,status,error,updated_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(object_kind,object_key) DO UPDATE SET
                       content_hash=excluded.content_hash,status=excluded.status,
                       error=excluded.error,updated_at=excluded.updated_at""",
                (object_kind, object_key, digest, status, error, utc_now()),
            )

    def ingest_state(self) -> tuple[dict[str, Any], ...]:
        with _connect(self.database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM ingest_state ORDER BY object_kind,object_key"
            ).fetchall()
        return tuple(dict(row) for row in rows)
