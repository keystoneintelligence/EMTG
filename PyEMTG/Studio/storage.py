from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid
from typing import Any, Mapping


SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


class StudioStore:
    def __init__(self, database_path: str | Path, workspace: str | Path, *, recover: bool = True):
        self.database_path = Path(database_path).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace = Path(workspace).resolve()
        self.runs_root = self.database_path.parent / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)
        with _connect(self.database_path) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    config_json TEXT NOT NULL,
                    source_root TEXT NOT NULL,
                    run_directory TEXT NOT NULL,
                    status TEXT NOT NULL,
                    queue_position INTEGER NOT NULL,
                    requested_cores INTEGER NOT NULL,
                    effective_cores INTEGER NOT NULL,
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, queue_position);
                CREATE TABLE IF NOT EXISTS solutions(
                    id TEXT PRIMARY KEY,
                    evaluation_key TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    candidate_id TEXT NOT NULL,
                    individual_id TEXT,
                    status TEXT NOT NULL,
                    feasible INTEGER NOT NULL,
                    pareto INTEGER NOT NULL DEFAULT 0,
                    fidelity TEXT,
                    trial INTEGER,
                    generation INTEGER,
                    role TEXT,
                    start_body TEXT,
                    end_body TEXT,
                    sequence_text TEXT,
                    launch_mjd REAL,
                    arrival_mjd REAL,
                    flight_time_days REAL,
                    propellant_used_kg REAL,
                    delivered_mass_kg REAL,
                    deterministic_delta_v_km_s REAL,
                    thrust_min_n REAL,
                    thrust_max_n REAL,
                    duty_cycle REAL,
                    active_engines INTEGER,
                    bus_power_kw REAL,
                    hardware_text TEXT,
                    candidate_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS solutions_filter ON solutions(
                    feasible, start_body, end_body, launch_mjd, arrival_mjd, propellant_used_kg
                );
                CREATE INDEX IF NOT EXISTS solutions_job ON solutions(job_id, generation, fidelity);
                CREATE TABLE IF NOT EXISTS trajectories(
                    solution_id TEXT NOT NULL REFERENCES solutions(id),
                    detail TEXT NOT NULL,
                    status TEXT NOT NULL,
                    artifact_path TEXT,
                    artifact_sha256 TEXT,
                    frame TEXT,
                    central_body TEXT,
                    sample_count INTEGER,
                    error TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(solution_id, detail)
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )
            connection.execute(
                "INSERT OR IGNORE INTO settings(key,value) VALUES('global_core_limit',?)",
                (str(max(1, (os.cpu_count() or 2) - 1)),),
            )
            if recover:
                # A process that disappeared during shutdown is safe to resume
                # from the outer-loop checkpoint. A requested pause remains paused.
                connection.execute("UPDATE jobs SET status='queued' WHERE status='running'")
                connection.execute("UPDATE jobs SET status='paused' WHERE status='pausing'")

    def global_core_limit(self) -> int:
        with _connect(self.database_path) as connection:
            return int(connection.execute(
                "SELECT value FROM settings WHERE key='global_core_limit'"
            ).fetchone()[0])

    def set_global_core_limit(self, value: int) -> None:
        if value < 1:
            raise ValueError("global core limit must be positive")
        with _connect(self.database_path) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO settings(key,value) VALUES('global_core_limit',?)",
                (str(value),),
            )
            connection.execute(
                "UPDATE jobs SET effective_cores=MIN(requested_cores, ?) WHERE status NOT IN ('completed','cancelled')",
                (value,),
            )

    def create_job(self, name: str, config: Mapping[str, Any], requested_cores: int, queue: bool) -> dict[str, Any]:
        if requested_cores < 1:
            raise ValueError("requested cores must be positive")
        identifier = uuid.uuid4().hex
        run_directory = (self.runs_root / identifier).resolve()
        position = 0
        with _connect(self.database_path) as connection:
            row = connection.execute("SELECT COALESCE(MAX(queue_position), -1) + 1 FROM jobs").fetchone()
            position = int(row[0])
            now = utc_now()
            effective = min(requested_cores, self.global_core_limit())
            connection.execute(
                """INSERT INTO jobs(
                    id,name,config_json,source_root,run_directory,status,queue_position,
                    requested_cores,effective_cores,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    identifier, name, json.dumps(dict(config), sort_keys=True), str(self.workspace),
                    str(run_directory), "queued" if queue else "draft", position,
                    requested_cores, effective, now, now,
                ),
            )
        run_directory.mkdir(parents=True, exist_ok=True)
        return self.job(identifier)

    @staticmethod
    def _job(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value["config"] = json.loads(value.pop("config_json"))
        value["progress"] = json.loads(value.pop("progress_json"))
        return value

    def job(self, identifier: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        return self._job(row)

    def jobs(self) -> list[dict[str, Any]]:
        with _connect(self.database_path) as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY queue_position, created_at").fetchall()
        return [self._job(row) for row in rows]

    def next_queued(self) -> dict[str, Any] | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE status='queued' ORDER BY queue_position,created_at LIMIT 1"
            ).fetchone()
        return None if row is None else self._job(row)

    def set_status(self, identifier: str, status: str, *, error: str | None = None) -> dict[str, Any]:
        allowed = {"draft","validating","queued","running","pausing","paused","completed","failed","cancelled"}
        if status not in allowed:
            raise ValueError(f"unknown job status {status}")
        now = utc_now()
        with _connect(self.database_path) as connection:
            current = connection.execute("SELECT status FROM jobs WHERE id=?", (identifier,)).fetchone()
            if current is None:
                raise KeyError(identifier)
            started = now if status == "running" and current["status"] != "running" else None
            finished = now if status in {"completed","failed","cancelled"} else None
            connection.execute(
                """UPDATE jobs SET status=?, error=?, updated_at=?,
                    started_at=COALESCE(started_at,?), finished_at=COALESCE(?,finished_at)
                    WHERE id=?""",
                (status, error, now, started, finished, identifier),
            )
        return self.job(identifier)

    def update_progress(self, identifier: str, progress: Mapping[str, Any]) -> None:
        with _connect(self.database_path) as connection:
            connection.execute(
                "UPDATE jobs SET progress_json=?,updated_at=? WHERE id=?",
                (json.dumps(dict(progress), sort_keys=True, default=str), utc_now(), identifier),
            )

    def set_requested_cores(self, identifier: str, requested: int) -> dict[str, Any]:
        if requested < 1:
            raise ValueError("requested cores must be positive")
        effective = min(requested, self.global_core_limit())
        with _connect(self.database_path) as connection:
            connection.execute(
                "UPDATE jobs SET requested_cores=?,effective_cores=?,updated_at=? WHERE id=?",
                (requested, effective, utc_now(), identifier),
            )
        return self.job(identifier)

    def move_job(self, identifier: str, direction: int) -> None:
        with _connect(self.database_path) as connection:
            current = connection.execute(
                "SELECT queue_position FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
            if current is None:
                raise KeyError(identifier)
            operator = "<" if direction < 0 else ">"
            ordering = "DESC" if direction < 0 else "ASC"
            neighbor = connection.execute(
                f"SELECT id,queue_position FROM jobs WHERE status IN ('queued','paused') AND queue_position {operator} ? ORDER BY queue_position {ordering} LIMIT 1",
                (current["queue_position"],),
            ).fetchone()
            if neighbor is not None:
                connection.execute("UPDATE jobs SET queue_position=? WHERE id=?", (neighbor["queue_position"], identifier))
                connection.execute("UPDATE jobs SET queue_position=? WHERE id=?", (current["queue_position"], neighbor["id"]))

    def delete_job(self, identifier: str) -> dict[str, Any]:
        """Delete one inactive campaign, its catalog rows, and its managed artifacts."""
        job = self.job(identifier)
        if job["status"] in {"running", "pausing"}:
            raise RuntimeError("running jobs must be cancelled before deletion")
        run_directory = Path(job["run_directory"]).resolve()
        try:
            run_directory.relative_to(self.runs_root.resolve())
        except ValueError as error:
            raise RuntimeError("job run directory is outside Studio's managed runs root") from error
        with _connect(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            solution_count = int(connection.execute(
                "SELECT COUNT(*) FROM solutions WHERE job_id=?", (identifier,)
            ).fetchone()[0])
            trajectory_count = int(connection.execute(
                """SELECT COUNT(*) FROM trajectories WHERE solution_id IN
                   (SELECT id FROM solutions WHERE job_id=?)""", (identifier,)
            ).fetchone()[0])
            connection.execute(
                "DELETE FROM trajectories WHERE solution_id IN (SELECT id FROM solutions WHERE job_id=?)",
                (identifier,),
            )
            connection.execute("DELETE FROM solutions WHERE job_id=?", (identifier,))
            connection.execute("DELETE FROM jobs WHERE id=?", (identifier,))
        if run_directory.is_dir():
            shutil.rmtree(run_directory)
        return {
            "deleted_job_id": identifier,
            "deleted_solutions": solution_count,
            "deleted_trajectories": trajectory_count,
            "deleted_run_directory": str(run_directory),
        }

    def upsert_solution(self, values: Mapping[str, Any]) -> None:
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        updates = ",".join(f"{name}=excluded.{name}" for name in columns if name not in {"id","evaluation_key","created_at"})
        with _connect(self.database_path) as connection:
            connection.execute(
                f"INSERT INTO solutions({','.join(columns)}) VALUES({placeholders}) ON CONFLICT(evaluation_key) DO UPDATE SET {updates}",
                tuple(values[name] for name in columns),
            )

    def solution(self, identifier: str) -> dict[str, Any]:
        with _connect(self.database_path) as connection:
            row = connection.execute("SELECT * FROM solutions WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError(identifier)
        value = dict(row)
        value["feasible"] = bool(value["feasible"])
        value["pareto"] = bool(value["pareto"])
        value["candidate"] = json.loads(value.pop("candidate_json"))
        value["result"] = json.loads(value.pop("result_json"))
        return value

    def solutions(self, filters: Mapping[str, Any], *, limit: int, offset: int) -> dict[str, Any]:
        clauses: list[str] = []
        parameters: list[Any] = []
        mapping = {
            "start_body": "start_body", "end_body": "end_body", "status": "status",
            "fidelity": "fidelity", "job_id": "job_id",
        }
        for key, column in mapping.items():
            if filters.get(key) not in {None, ""}:
                clauses.append(f"{column}=?")
                parameters.append(filters[key])
        if filters.get("feasible") is not None:
            clauses.append("feasible=?")
            parameters.append(1 if filters["feasible"] else 0)
        for key, column, op in (
            ("launch_after", "launch_mjd", ">="), ("launch_before", "launch_mjd", "<="),
            ("arrival_after", "arrival_mjd", ">="), ("arrival_before", "arrival_mjd", "<="),
            ("propellant_min", "propellant_used_kg", ">="), ("propellant_max", "propellant_used_kg", "<="),
            ("thrust_min", "thrust_max_n", ">="), ("thrust_max", "thrust_min_n", "<="),
        ):
            if filters.get(key) is not None:
                clauses.append(f"{column}{op}?")
                parameters.append(float(filters[key]))
        if filters.get("sequence"):
            clauses.append("sequence_text LIKE ?")
            parameters.append(f"%{filters['sequence']}%")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with _connect(self.database_path) as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM solutions" + where, tuple(parameters)).fetchone()[0])
            rows = connection.execute(
                "SELECT * FROM solutions" + where + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["feasible"] = bool(item["feasible"])
            item["pareto"] = bool(item["pareto"])
            item.pop("candidate_json", None)
            item.pop("result_json", None)
            items.append(item)
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    def mark_trajectory(self, solution_id: str, detail: str, **values: Any) -> None:
        payload = {
            "solution_id": solution_id, "detail": detail,
            "status": values.get("status", "available"),
            "artifact_path": values.get("artifact_path"),
            "artifact_sha256": values.get("artifact_sha256"),
            "frame": values.get("frame", "J2000"),
            "central_body": values.get("central_body"),
            "sample_count": values.get("sample_count"),
            "error": values.get("error"), "updated_at": utc_now(),
        }
        columns = tuple(payload)
        with _connect(self.database_path) as connection:
            connection.execute(
                f"INSERT OR REPLACE INTO trajectories({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                tuple(payload[name] for name in columns),
            )

    def trajectory(self, solution_id: str, detail: str) -> dict[str, Any] | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM trajectories WHERE solution_id=? AND detail=?", (solution_id, detail)
            ).fetchone()
        return None if row is None else dict(row)

    def next_materialization(self) -> dict[str, Any] | None:
        with _connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT t.*,s.job_id,s.candidate_id FROM trajectories t
                   JOIN solutions s ON s.id=t.solution_id
                   WHERE t.detail='dense' AND t.status='requested'
                   ORDER BY t.updated_at LIMIT 1"""
            ).fetchone()
            if row is not None:
                connection.execute(
                    "UPDATE trajectories SET status='running',updated_at=? WHERE solution_id=? AND detail='dense'",
                    (utc_now(), row["solution_id"]),
                )
        return None if row is None else dict(row)
