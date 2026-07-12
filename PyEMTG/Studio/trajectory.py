from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Iterable

from .ephemeris import SpiceEphemerisProvider
from .models import TrajectorySeries
from .storage import StudioStore


@dataclass(frozen=True)
class OutputFrameMetadata:
    frame: str
    central_body: str | None
    alpha0: float | None
    delta0: float | None
    time_system: str = "TDB"


def parse_output_frame_metadata(path: str | Path) -> OutputFrameMetadata:
    """Read the reference frame EMTG used when writing a mission output.

    EMTG may write one header per journey. A single trajectory series can only
    be normalized safely when every journey uses the same output transform.
    """
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    time_system = "TDB"
    with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if line.startswith("Journey:"):
                if current and current.get("frame"):
                    blocks.append(current)
                current = {}
            elif current is not None and line.startswith("Central Body:"):
                current["central_body"] = line.split(":", 1)[1].strip() or None
            elif current is not None and line.startswith("Frame:"):
                current["frame"] = line.split(":", 1)[1].strip()
            elif current is not None and line.startswith("alpha0:"):
                current["alpha0"] = float(line.split(":", 1)[1].strip())
            elif current is not None and line.startswith("delta0:"):
                current["delta0"] = float(line.split(":", 1)[1].strip())
            if "(ET/TDB)" in line:
                time_system = "TDB"
    if current and current.get("frame"):
        blocks.append(current)
    if not blocks:
        raise ValueError(f"EMTG output does not declare a trajectory frame: {path}")
    unique = {
        (
            str(block["frame"]).upper(),
            block.get("central_body"),
            block.get("alpha0"),
            block.get("delta0"),
        )
        for block in blocks
    }
    if len(unique) != 1:
        raise ValueError("EMTG output contains multiple journey frame transforms; refusing to mix them")
    frame, central_body, alpha0, delta0 = unique.pop()
    return OutputFrameMetadata(frame, central_body, alpha0, delta0, time_system)


def _matrix_multiply(left: tuple[tuple[float, ...], ...], right: tuple[tuple[float, ...], ...]) -> tuple[tuple[float, ...], ...]:
    return tuple(
        tuple(sum(left[row][inner] * right[inner][column] for inner in range(3)) for column in range(3))
        for row in range(3)
    )


def _j2000_bci_to_icrf(alpha0: float, delta0: float) -> tuple[tuple[float, ...], ...]:
    x_angle = math.pi / 2.0 - delta0
    z_angle = math.pi / 2.0 + alpha0
    rx = (
        (1.0, 0.0, 0.0),
        (0.0, math.cos(x_angle), -math.sin(x_angle)),
        (0.0, math.sin(x_angle), math.cos(x_angle)),
    )
    rz = (
        (math.cos(z_angle), -math.sin(z_angle), 0.0),
        (math.sin(z_angle), math.cos(z_angle), 0.0),
        (0.0, 0.0, 1.0),
    )
    return _matrix_multiply(rz, rx)


def _rotate_vector(values: Any, matrix: tuple[tuple[float, ...], ...]) -> Any:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return values
    vector = tuple(float(value) for value in values)
    return [sum(matrix[row][column] * vector[column] for column in range(3)) for row in range(3)]


def normalize_samples_to_icrf(
    samples: list[dict[str, Any]], metadata: OutputFrameMetadata,
) -> tuple[list[dict[str, Any]], str]:
    source_frame = metadata.frame.upper()
    if source_frame in {"ICRF", "J2000", "J2000_ICRF", "J2000/ICRF"}:
        return samples, "identity"
    if source_frame != "J2000_BCI":
        raise ValueError(f"EMTG output frame {metadata.frame!r} cannot be normalized to J2000/ICRF")
    if metadata.alpha0 is None or metadata.delta0 is None:
        raise ValueError("J2000_BCI output is missing alpha0/delta0; refusing an unsafe overlay")
    matrix = _j2000_bci_to_icrf(metadata.alpha0, metadata.delta0)
    transformed: list[dict[str, Any]] = []
    for sample in samples:
        value = dict(sample)
        for field in ("position_km", "velocity_km_s", "control", "thrust_n"):
            value[field] = _rotate_vector(value.get(field), matrix)
        transformed.append(value)
    return transformed, "J2000_BCI to ICRF (Rz(pi/2+alpha0) * Rx(pi/2-delta0))"


def trajectory_endpoints_align(
    dense: list[dict[str, Any]], events: list[dict[str, Any]],
    *, position_tolerance_km: float = 10.0, epoch_tolerance_days: float = 1.0e-3,
) -> bool:
    """Confirm a derived ephemeris represents the same boundary-value case."""
    if not dense or not events:
        return False
    for dense_sample, event_sample in ((dense[0], events[0]), (dense[-1], events[-1])):
        if abs(float(dense_sample["epoch_mjd"]) - float(event_sample["epoch_mjd"])) > epoch_tolerance_days:
            return False
        dense_position = dense_sample.get("position_km")
        event_position = event_sample.get("position_km")
        if not isinstance(dense_position, list) or not isinstance(event_position, list):
            return False
        separation = math.sqrt(sum((float(dense_position[index]) - float(event_position[index])) ** 2 for index in range(3)))
        if separation > position_tolerance_km:
            return False
    return True


def _downsample(values: list[dict[str, Any]], maximum: int) -> list[dict[str, Any]]:
    if maximum < 2 or len(values) <= maximum:
        return values
    step = (len(values) - 1) / (maximum - 1)
    indexes = sorted({0, len(values) - 1, *(round(index * step) for index in range(maximum))})
    return [values[index] for index in indexes]


def _event_samples(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for event in events:
        position = event.get("position_km")
        epoch = event.get("julian_date_mjd")
        if not isinstance(position, list) or len(position) != 3 or epoch is None:
            continue
        output.append({
            "epoch_mjd": float(epoch), "position_km": [float(value) for value in position],
            "velocity_km_s": event.get("velocity_km_s"), "event_type": event.get("event_type"),
            "location": event.get("location"), "mass_kg": event.get("mass"),
            "control": event.get("control"), "thrust_n": event.get("thrust_n"),
            "thrust_magnitude_n": event.get("thrust_magnitude_n"),
            "available_thrust_n": event.get("available_thrust_n"), "isp_s": event.get("isp_s"),
            "mass_flow_rate_kg_s": event.get("mass_flow_rate_kg_s"),
            "active_engines": event.get("active_engines"), "active_power_kw": event.get("active_power_kw"),
            "available_power_kw": event.get("available_power_kw"),
        })
    return output


def parse_dense_ephemeris(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8", errors="replace") as stream:
        header = next(stream, "").lstrip("#").strip().split(",")
        names = [value.strip() for value in header]
        for line in stream:
            fields = [value.strip() for value in line.split(",")]
            if len(fields) < 7:
                continue
            try:
                moment = datetime.strptime(fields[0], "%Y %b %d  %H:%M:%S.%f").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    moment = datetime.strptime(fields[0], "%Y %b %d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
            epoch_mjd = moment.timestamp() / 86400.0 + 40587.0
            try:
                numeric = [float(value) for value in fields[1:]]
            except ValueError:
                continue
            row: dict[str, Any] = {
                "epoch_mjd": epoch_mjd,
                "position_km": numeric[0:3],
                "velocity_km_s": numeric[3:6],
            }
            for index, name in enumerate(names[7:], start=6):
                if index < len(numeric):
                    row[name] = numeric[index]
            rows.append(row)
    return rows


class TrajectoryService:
    def __init__(self, store: StudioStore):
        self.store = store

    def get(self, solution_id: str, detail: str, frame: str, maximum: int) -> TrajectorySeries:
        if frame.upper() not in {"J2000", "ICRF"}:
            raise ValueError("only J2000/ICRF is available until a configured SPICE provider can transform this solution")
        solution = self.store.solution(solution_id)
        job = self.store.job(solution["job_id"])
        artifacts = solution["result"].get("artifacts", {})
        mission_output = next(
            (
                Path(str(value)) for value in artifacts.values()
                if Path(str(value)).suffix.lower() == ".emtg"
            ),
            None,
        )
        if mission_output is not None and mission_output.is_file():
            event_metadata = parse_output_frame_metadata(mission_output)
        else:
            evaluator_type = str(job.get("config", {}).get("evaluator", {}).get("type", "")).lower()
            if evaluator_type != "synthetic":
                raise ValueError("the EMTG mission artifact needed to establish the trajectory frame is unavailable")
            event_metadata = OutputFrameMetadata("SYNTHETIC", None, None, None, "TDB")
        selected_detail = detail
        samples: list[dict[str, Any]] = []
        sample_metadata = event_metadata
        materialization_status = "event_fallback"
        if detail in {"auto", "dense"}:
            dense = self.store.trajectory(solution_id, "dense")
            if dense and dense.get("status") == "available" and dense.get("artifact_path"):
                path = Path(str(dense["artifact_path"]))
                if path.is_file():
                    samples = parse_dense_ephemeris(path)
                    selected_detail = "dense"
                    dense_frame = str(dense.get("frame") or "").upper()
                    if dense_frame in {"ICRF", "J2000", "J2000_ICRF", "J2000/ICRF"}:
                        sample_metadata = OutputFrameMetadata(
                            "J2000/ICRF", dense.get("central_body") or event_metadata.central_body,
                            None, None, event_metadata.time_system,
                        )
                    else:
                        sample_metadata = event_metadata
        if not samples:
            selected_detail = "events"
            samples = _event_samples(solution["result"].get("metrics", {}).get("mission_events", ()))
            sample_metadata = event_metadata
        if sample_metadata.frame == "SYNTHETIC":
            transformation = "synthetic debug coordinates (no physical frame transform)"
        else:
            samples, transformation = normalize_samples_to_icrf(samples, sample_metadata)
        if selected_detail == "dense":
            event_samples = _event_samples(solution["result"].get("metrics", {}).get("mission_events", ()))
            if event_metadata.frame != "SYNTHETIC":
                event_samples, _ = normalize_samples_to_icrf(event_samples, event_metadata)
            if trajectory_endpoints_align(samples, event_samples):
                materialization_status = "available"
            else:
                samples = event_samples
                sample_metadata = event_metadata
                selected_detail = "events"
                transformation = "dense artifact rejected: boundary mismatch; using normalized EMTG events"
                materialization_status = "dense_rejected_endpoint_mismatch"
        original = len(samples)
        samples = _downsample(samples, max(2, min(maximum, 50000)))
        universe_folder = Path(str(job.get("config", {}).get("assets", {}).get("universe_folder", "")))
        leap_second_kernels = sorted((universe_folder / "ephemeris_files").glob("*.tls"))
        if leap_second_kernels and samples:
            try:
                offsets = SpiceEphemerisProvider(leap_second_kernels).tdb_minus_utc_seconds(
                    [float(sample["epoch_mjd"]) for sample in samples]
                )
                for sample, offset in zip(samples, offsets):
                    sample["tdb_minus_utc_seconds"] = offset
            except Exception:
                # Geometry remains usable without an LSK, but the frontend
                # will label the numeric epoch as TDB rather than claim UTC.
                pass
        return TrajectorySeries(
            solution_id=solution_id,
            detail=selected_detail,
            frame="J2000/ICRF",
            source_frame=sample_metadata.frame,
            central_body=sample_metadata.central_body,
            time_system=sample_metadata.time_system,
            source_time_system=sample_metadata.time_system,
            transformation_applied=transformation,
            samples=samples,
            original_count=original,
            returned_count=len(samples),
            materialization_status=materialization_status,
        )
