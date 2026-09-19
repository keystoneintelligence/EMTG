"""Bounded subprocess execution with process-tree cleanup."""

from __future__ import annotations

from dataclasses import dataclass, field
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Mapping, Sequence


@dataclass(frozen=True)
class ProcessOutcome:
    arguments: tuple[str, ...]
    returncode: int | None
    timed_out: bool
    cancelled: bool
    runtime_seconds: float
    stdout_path: str
    stderr_path: str
    stdout_tail: str
    stderr_tail: str
    resource_statistics: Mapping[str, float] = field(default_factory=dict)


class _WindowsJob:
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JobObjectExtendedLimitInformation = 9

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimit(ctypes.Structure):
        pass

    _ExtendedLimit._fields_ = [
        ("BasicLimitInformation", _BasicLimit),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]

    def __init__(self, *, memory_bytes: int | None = None, max_processes: int | None = None) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32 = kernel32
        self._handle = kernel32.CreateJobObjectW(None, None)
        if not self._handle:
            raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
        information = self._ExtendedLimit()
        flags = self.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if memory_bytes is not None:
            flags |= self.JOB_OBJECT_LIMIT_PROCESS_MEMORY | self.JOB_OBJECT_LIMIT_JOB_MEMORY
            information.ProcessMemoryLimit = int(memory_bytes)
            information.JobMemoryLimit = int(memory_bytes)
        if max_processes is not None:
            flags |= self.JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            information.BasicLimitInformation.ActiveProcessLimit = int(max_processes)
        information.BasicLimitInformation.LimitFlags = flags
        if not kernel32.SetInformationJobObject(
            self._handle,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            self.close()
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        if not self._kernel32.AssignProcessToJobObject(self._handle, int(process._handle)):  # type: ignore[attr-defined]
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "AssignProcessToJobObject failed")

    def statistics(self) -> dict[str, float]:
        information = self._ExtendedLimit()
        returned = ctypes.c_uint32()
        ok = self._kernel32.QueryInformationJobObject(
            self._handle,
            self.JobObjectExtendedLimitInformation,
            ctypes.byref(information),
            ctypes.sizeof(information),
            ctypes.byref(returned),
        )
        if not ok:
            return {}
        return {
            "peak_process_memory_bytes": float(information.PeakProcessMemoryUsed),
            "peak_job_memory_bytes": float(information.PeakJobMemoryUsed),
            "read_bytes": float(information.IoInfo.ReadTransferCount),
            "write_bytes": float(information.IoInfo.WriteTransferCount),
        }

    def terminate(self) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if getattr(self, "_handle", None):
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _tail(path: Path, limit: int = 16384) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def run_process(
    arguments: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | Path,
    timeout_seconds: float,
    stdout_path: str | Path,
    stderr_path: str | Path,
    environment: Mapping[str, str] | None = None,
    cancel_event: threading.Event | None = None,
    cpu_seconds: int | None = None,
    memory_bytes: int | None = None,
    max_processes: int | None = None,
) -> ProcessOutcome:
    if not arguments:
        raise ValueError("process arguments are empty")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    argv = tuple(os.fspath(value) for value in arguments)
    work = Path(cwd).resolve()
    stdout_file = Path(stdout_path).resolve()
    stderr_file = Path(stderr_path).resolve()
    stdout_file.parent.mkdir(parents=True, exist_ok=True)
    stderr_file.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
    })
    if environment:
        env.update({str(key): str(value) for key, value in environment.items()})
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    if cpu_seconds is not None and cpu_seconds <= 0:
        raise ValueError("cpu_seconds must be positive")
    if memory_bytes is not None and memory_bytes <= 0:
        raise ValueError("memory_bytes must be positive")
    if max_processes is not None and max_processes <= 0:
        raise ValueError("max_processes must be positive")
    preexec_fn = None
    if os.name != "nt" and any(value is not None for value in (cpu_seconds, memory_bytes, max_processes)):
        def apply_limits() -> None:
            import resource
            if cpu_seconds is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if memory_bytes is not None:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
            if max_processes is not None and hasattr(resource, "RLIMIT_NPROC"):
                resource.setrlimit(resource.RLIMIT_NPROC, (max_processes, max_processes))
        preexec_fn = apply_limits
    job: _WindowsJob | None = None
    start_usage = None
    if os.name != "nt":
        import resource
        start_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.monotonic()
    timed_out = False
    cancelled = False
    with stdout_file.open("wb") as stdout, stderr_file.open("wb") as stderr:
        process = subprocess.Popen(
            argv,
            cwd=work,
            stdout=stdout,
            stderr=stderr,
            env=env,
            shell=False,
            creationflags=flags,
            start_new_session=(os.name != "nt"),
            preexec_fn=preexec_fn,
        )
        if os.name == "nt":
            try:
                job = _WindowsJob(memory_bytes=memory_bytes, max_processes=max_processes)
                job.assign(process)
            except OSError:
                process.kill()
                process.wait(timeout=5.0)
                raise
        deadline = start + timeout_seconds
        while process.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.05)
        if timed_out or cancelled:
            if job is not None:
                job.terminate()
            elif os.name == "nt":
                process.kill()
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        try:
            returncode = process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            returncode = process.wait()
    runtime = time.monotonic() - start
    statistics = job.statistics() if job is not None else {}
    if job is not None:
        job.close()
    if os.name != "nt" and start_usage is not None:
        import resource
        end_usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        statistics.update({
            "user_cpu_seconds": end_usage.ru_utime - start_usage.ru_utime,
            "system_cpu_seconds": end_usage.ru_stime - start_usage.ru_stime,
            "maximum_resident_set": float(end_usage.ru_maxrss),
        })
    return ProcessOutcome(
        argv,
        returncode,
        timed_out,
        cancelled,
        runtime,
        str(stdout_file),
        str(stderr_file),
        _tail(stdout_file),
        _tail(stderr_file),
        statistics,
    )
