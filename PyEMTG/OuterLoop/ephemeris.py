"""SPK body coverage manifests without loading kernels into EMTG workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping


J2000_MJD = 51544.5


@dataclass(frozen=True)
class CoverageInterval:
    start_mjd: float
    end_mjd: float

    def covers(self, start_mjd: float, end_mjd: float) -> bool:
        return self.start_mjd <= start_mjd and self.end_mjd >= end_mjd


class EphemerisCoverage:
    def __init__(self, intervals: Mapping[int, Iterable[CoverageInterval]], kernels: Iterable[Path]):
        self.intervals = {
            int(spice_id): self._merge(tuple(values))
            for spice_id, values in intervals.items()
        }
        self.kernels = tuple(sorted(Path(path).resolve() for path in kernels))

    @staticmethod
    def _merge(values: tuple[CoverageInterval, ...]) -> tuple[CoverageInterval, ...]:
        output: list[CoverageInterval] = []
        for value in sorted(values, key=lambda interval: (interval.start_mjd, interval.end_mjd)):
            if output and value.start_mjd <= output[-1].end_mjd:
                output[-1] = CoverageInterval(output[-1].start_mjd, max(output[-1].end_mjd, value.end_mjd))
            else:
                output.append(value)
        return tuple(output)

    @classmethod
    def from_directory(
        cls,
        directory: str | Path,
        *,
        brief_executable: str | Path | None = None,
    ) -> "EphemerisCoverage":
        root = Path(directory).resolve()
        kernels = tuple(sorted(root.glob("*.bsp")))
        if not kernels:
            raise ValueError(f"no BSP kernels found in {root}")
        try:
            intervals = _spiceypy_coverage(kernels)
        except (ImportError, OSError, RuntimeError):
            if brief_executable is None:
                raise ValueError("SPK coverage requires spiceypy or a configured brief executable")
            intervals = _brief_coverage(kernels, Path(brief_executable).resolve())
        return cls(intervals, kernels)

    def covers(self, spice_id: int, start_mjd: float, end_mjd: float) -> bool:
        return any(interval.covers(start_mjd, end_mjd) for interval in self.intervals.get(int(spice_id), ()))

    def missing(self, spice_ids: Iterable[int], start_mjd: float, end_mjd: float) -> tuple[int, ...]:
        return tuple(
            spice_id
            for spice_id in sorted(set(map(int, spice_ids)))
            if not self.covers(spice_id, start_mjd, end_mjd)
        )

    def manifest(self) -> dict[str, object]:
        return {
            "kernels": [str(path) for path in self.kernels],
            "bodies": {
                str(spice_id): [[interval.start_mjd, interval.end_mjd] for interval in values]
                for spice_id, values in sorted(self.intervals.items())
            },
        }


def _spiceypy_coverage(kernels: Iterable[Path]) -> dict[int, list[CoverageInterval]]:
    import spiceypy
    from spiceypy.utils.support_types import SPICEDOUBLE_CELL

    intervals: dict[int, list[CoverageInterval]] = {}
    for kernel in kernels:
        try:
            objects = tuple(int(value) for value in spiceypy.spkobj(str(kernel)))
        except Exception as error:
            raise RuntimeError(f"cannot inspect {kernel}: {error}") from error
        for spice_id in objects:
            window = SPICEDOUBLE_CELL(200000)
            spiceypy.spkcov(str(kernel), spice_id, window)
            for index in range(spiceypy.wncard(window)):
                start_et, end_et = spiceypy.wnfetd(window, index)
                intervals.setdefault(spice_id, []).append(
                    CoverageInterval(J2000_MJD + start_et / 86400.0, J2000_MJD + end_et / 86400.0)
                )
    return intervals

def _brief_coverage(
    kernels: Iterable[Path], executable: Path
) -> dict[int, list[CoverageInterval]]:
    if not executable.is_file():
        raise ValueError(f"brief executable does not exist: {executable}")
    intervals: dict[int, list[CoverageInterval]] = {}
    row = re.compile(r"^\s*(-?\d+)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*$")
    same = re.compile(r"^\s*(-?\d+)\s+Same coverage as previous object\s*$")
    for kernel in kernels:
        completed = subprocess.run(
            [str(executable), "-t", "-n", "-etsec", str(kernel)],
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
            shell=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"brief failed for {kernel}: {completed.stderr[-1000:]}")
        previous: CoverageInterval | None = None
        for line in completed.stdout.splitlines():
            match = row.match(line)
            if match:
                spice_id = int(match.group(1))
                previous = CoverageInterval(
                    J2000_MJD + float(match.group(2)) / 86400.0,
                    J2000_MJD + float(match.group(3)) / 86400.0,
                )
                intervals.setdefault(spice_id, []).append(previous)
                continue
            match = same.match(line)
            if match and previous is not None:
                intervals.setdefault(int(match.group(1)), []).append(previous)
    if not intervals:
        raise ValueError("brief produced no parseable SPK coverage")
    return intervals
