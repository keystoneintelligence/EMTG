"""Reference-frame provider boundary used by the Studio scene service."""

from __future__ import annotations

from pathlib import Path
import threading
from typing import Mapping, Protocol, Sequence


def _mjd_tdb_to_et(epoch_mjd: float) -> float:
    """Convert EMTG's ET/TDB modified Julian date directly to SPICE ET."""
    return (float(epoch_mjd) - 51544.5) * 86400.0


class EphemerisProvider(Protocol):
    def states(
        self,
        bodies: Mapping[str, int],
        epochs_mjd: Sequence[float],
        *,
        observer_spice_id: int,
        frame: str = "J2000",
    ) -> Mapping[str, list[list[float]]]: ...


class SpiceEphemerisProvider:
    """SPICE body-state provider with explicit kernel ownership.

    Kernels are furnished lazily and calls are serialized because CSPICE owns
    process-global kernel state. Missing coverage is surfaced by SpiceyPy;
    callers must not silently substitute a different reference frame.
    """

    _lock = threading.RLock()
    _loaded: set[Path] = set()

    def __init__(self, kernels: Sequence[str | Path]):
        self.kernels = tuple(Path(value).resolve() for value in kernels)
        missing = [path for path in self.kernels if not path.is_file()]
        if missing:
            raise FileNotFoundError(missing[0])

    def _furnish(self) -> None:
        import spiceypy
        with self._lock:
            for path in self.kernels:
                if path not in self._loaded:
                    spiceypy.furnsh(str(path))
                    self._loaded.add(path)

    def states(
        self,
        bodies: Mapping[str, int],
        epochs_mjd: Sequence[float],
        *,
        observer_spice_id: int,
        frame: str = "J2000",
    ) -> Mapping[str, list[list[float]]]:
        import spiceypy
        self._furnish()
        output: dict[str, list[list[float]]] = {name: [] for name in bodies}
        with self._lock:
            for epoch in epochs_mjd:
                et = _mjd_tdb_to_et(epoch)
                for name, spice_id in bodies.items():
                    state, _ = spiceypy.spkezr(str(spice_id), et, frame, "NONE", str(observer_spice_id))
                    output[name].append([float(value) for value in state])
        return output

    def tdb_minus_utc_seconds(self, epochs_mjd: Sequence[float]) -> list[float]:
        """Return the SPICE ET/TDB minus UTC offset at each mission epoch."""
        import spiceypy
        self._furnish()
        with self._lock:
            return [
                float(spiceypy.deltet(_mjd_tdb_to_et(epoch), "ET"))
                for epoch in epochs_mjd
            ]
