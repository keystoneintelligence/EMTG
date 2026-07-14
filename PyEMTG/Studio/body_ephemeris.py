from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .bodies import discover_bodies
from .ephemeris import SpiceEphemerisProvider


class BodyEphemerisService:
    def __init__(self, config: Mapping[str, Any]):
        self.config = config
        self.catalog = discover_bodies(config)
        self.universe_folder = Path(str(config.get("assets", {}).get("universe_folder", ""))).resolve()

    @staticmethod
    def _epochs(start_mjd: float, end_mjd: float, points: int) -> list[float]:
        if end_mjd <= start_mjd:
            raise ValueError("body ephemeris end time must be later than start time")
        count = max(2, min(int(points), 2000))
        step = (end_mjd - start_mjd) / (count - 1)
        return [start_mjd + index * step for index in range(count)]

    def series(
        self,
        names: Sequence[str],
        start_mjd: float,
        end_mjd: float,
        points: int,
        frame: str = "J2000",
    ) -> dict[str, Any]:
        if frame.upper() not in {"J2000", "ICRF"}:
            raise ValueError("body ephemerides currently support only J2000/ICRF")
        options = {str(value["name"]): value for value in self.catalog["items"]}
        selected_names = list(dict.fromkeys(str(value) for value in names if str(value)))
        if not selected_names:
            raise ValueError("at least one body name is required")
        unknown = [value for value in selected_names if value not in options]
        if unknown:
            raise ValueError(f"body is not kernel-backed in the active universe: {', '.join(unknown)}")

        kernel_root = self.universe_folder / "ephemeris_files"
        selected_kernel_names = {
            str(options[name]["kernel_files"][0])
            for name in selected_names
        }
        if (kernel_root / "de430.bsp").is_file():
            selected_kernel_names.add("de430.bsp")
        kernels = [kernel_root / value for value in sorted(selected_kernel_names)]
        for pattern in ("*.tls", "*.tpc"):
            kernels.extend(sorted(kernel_root.glob(pattern)))
        provider = SpiceEphemerisProvider(kernels)
        ids = {name: int(options[name]["spice_id"]) for name in selected_names}
        requested_start, requested_end = float(start_mjd), float(end_mjd)
        if requested_end <= requested_start:
            raise ValueError("body ephemeris end time must be later than start time")
        import spiceypy
        body_series = []
        for name in selected_names:
            source_kernel = kernel_root / str(options[name]["kernel_files"][0])
            coverage = list(spiceypy.spkcov(str(source_kernel), ids[name]))
            intervals = [
                (51544.5 + float(coverage[index]) / 86400.0, 51544.5 + float(coverage[index + 1]) / 86400.0)
                for index in range(0, len(coverage), 2)
            ]
            overlaps = [
                (max(requested_start, lower), min(requested_end, upper))
                for lower, upper in intervals
                if min(requested_end, upper) > max(requested_start, lower)
            ]
            common = {
                "name": name,
                "display_name": options[name]["display_name"],
                "spice_id": ids[name],
                "category": options[name]["category"],
                "coverage_intervals_mjd": [[lower, upper] for lower, upper in intervals],
            }
            if not overlaps:
                body_series.append({**common, "coverage_status": "uncovered", "samples": []})
                continue
            overlap_start, overlap_end = max(overlaps, key=lambda value: value[1] - value[0])
            epochs = self._epochs(overlap_start, overlap_end, points)
            try:
                states = provider.states(
                    {name: ids[name]}, epochs,
                    observer_spice_id=int(self.catalog["central_spice_id"]),
                    frame="J2000",
                )[name]
                samples = [
                    {"epoch_mjd": epoch, "position_km": [float(value) for value in state[:3]]}
                    for epoch, state in zip(epochs, states)
                ]
                status = "covered" if overlap_start <= requested_start and overlap_end >= requested_end else "partial"
                body_series.append({
                    **common, "coverage_status": status,
                    "coverage_start_mjd": overlap_start, "coverage_end_mjd": overlap_end,
                    "samples": samples,
                })
            except Exception as error:
                body_series.append({**common, "coverage_status": "error", "error": str(error), "samples": []})
        return {
            "frame": "J2000",
            "time_system": "MJD",
            "central_body": self.catalog["central_body"],
            "observer_spice_id": int(self.catalog["central_spice_id"]),
            "start_mjd": requested_start,
            "end_mjd": requested_end,
            "sample_count": max((len(value["samples"]) for value in body_series), default=0),
            "kernel_files": [str(value) for value in kernels],
            "series": body_series,
        }

    def current_series(
        self,
        names: Sequence[str],
        points: int = 97,
        window_days: float = 2.0,
        frame: str = "J2000",
        *,
        moment: datetime | None = None,
    ) -> dict[str, Any]:
        """Return body tracks centered on the actual current UTC instant."""
        span = float(window_days)
        if span <= 0.0:
            raise ValueError("current body ephemeris window must be positive")
        current_utc = moment or datetime.now(timezone.utc)
        if current_utc.tzinfo is None:
            raise ValueError("current body ephemeris time must be timezone-aware")
        current_utc = current_utc.astimezone(timezone.utc)
        leap_seconds = sorted((self.universe_folder / "ephemeris_files").glob("*.tls"))
        if not leap_seconds:
            raise FileNotFoundError("no SPICE leap-second kernel is available")
        current_epoch = SpiceEphemerisProvider(leap_seconds).tdb_mjd_from_utc(current_utc)
        result = self.series(
            names,
            current_epoch - span / 2.0,
            current_epoch + span / 2.0,
            points,
            frame,
        )
        result["current_epoch_mjd"] = current_epoch
        result["current_utc"] = current_utc.isoformat().replace("+00:00", "Z")
        return result
