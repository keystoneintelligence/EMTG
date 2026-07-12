from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .catalog import SolutionCatalog
from .storage import StudioStore


class StudioScheduler:
    def __init__(self, store: StudioStore):
        self.store = store
        self.catalog = SolutionCatalog(store)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: subprocess.Popen[str] | None = None
        self._active_job: str | None = None
        self._active_materialization: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="emtg-studio-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        if self._process is not None and self._process.poll() is None:
            self._process.terminate()

    def wake(self) -> None:
        self._wake.set()

    def _worker_command(self, job_id: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--worker-database", str(self.store.database_path), "--worker-job", job_id]
        return [sys.executable, "-m", "PyEMTG.Studio.worker", "--database", str(self.store.database_path), "--job", job_id]

    def _materialization_command(self, solution_id: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--materialize-database", str(self.store.database_path), "--materialize-solution", solution_id]
        return [sys.executable, "-m", "PyEMTG.Studio.materialize", "--database", str(self.store.database_path), "--solution", solution_id]

    def interrupt(self, job_id: str, final_status: str) -> None:
        if self._active_job == job_id and self._process is not None and self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self._process.kill()
            try:
                self.catalog.ingest_job(job_id)
            except Exception:
                pass
            self.store.set_status(job_id, final_status)
            self._process = None
            self._active_job = None
            self.wake()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._process is not None:
                code = self._process.poll()
                if code is not None:
                    try:
                        if self._active_job:
                            self.catalog.ingest_job(self._active_job)
                    except Exception:
                        pass
                    self._process = None
                    self._active_job = None
                    self._active_materialization = None
                else:
                    self._wake.wait(0.25)
                    self._wake.clear()
                    continue
            job = self.store.next_queued()
            if job is None:
                materialization = self.store.next_materialization()
                if materialization is not None:
                    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    self._process = subprocess.Popen(
                        self._materialization_command(materialization["solution_id"]),
                        cwd=str(self.store.workspace), text=True, creationflags=flags,
                    )
                    self._active_materialization = materialization["solution_id"]
                    continue
                self._wake.wait(0.5)
                self._wake.clear()
                continue
            self.store.set_status(job["id"], "running")
            environment = dict(os.environ)
            for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
                environment[name] = "1"
            flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self._process = subprocess.Popen(
                self._worker_command(job["id"]),
                cwd=str(self.store.workspace),
                env=environment,
                text=True,
                creationflags=flags,
            )
            self._active_job = job["id"]
