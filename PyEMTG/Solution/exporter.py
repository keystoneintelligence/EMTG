from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
import zipfile

try:
    from ..OuterLoop.evaluator import EMTGResultParser
except ImportError:  # Historical EMTG scripts place PyEMTG itself on sys.path.
    from OuterLoop.evaluator import EMTGResultParser


CONTRACT_VERSION = 1
CONTRACT_SCHEMA = "https://schemas.keystoneintelligence.ai/deepspace/package-v1.json"
EMTG_SOLUTION_KIND = "org.keystone.emtg.solution"


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_role(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9._:-]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise ValueError(f"artifact role is empty after normalization: {value!r}")
    return normalized[:128]


def _output_frame(path: Path) -> tuple[str, str | None]:
    frames: set[str] = set()
    central_bodies: set[str] = set()
    current_journey = False
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for raw_line in stream:
            line = raw_line.strip()
            if line.startswith("Journey:"):
                current_journey = True
            elif current_journey and line.startswith("Central Body:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    central_bodies.add(value)
            elif current_journey and line.startswith("Frame:"):
                value = line.split(":", 1)[1].strip()
                if value:
                    frames.add(value)
    frame = next(iter(frames)) if len(frames) == 1 else "EMTG_OUTPUT"
    central_body = next(iter(central_bodies)) if len(central_bodies) == 1 else None
    return frame, central_body


def _trajectory_document(
    solution_id: str,
    metrics: Mapping[str, Any],
    mission_output: Path,
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for event in metrics.get("mission_events", ()):
        if not isinstance(event, Mapping):
            continue
        position = event.get("position_km")
        epoch = event.get("julian_date_mjd")
        if (
            not isinstance(position, (list, tuple))
            or len(position) != 3
            or epoch is None
        ):
            continue
        samples.append(
            {
                "epoch_mjd": float(epoch),
                "position_km": [float(value) for value in position],
                "velocity_km_s": event.get("velocity_km_s"),
                "event_type": event.get("event_type"),
                "location": event.get("location"),
                "mass_kg": event.get("mass"),
                "control": event.get("control"),
                "thrust_n": event.get("thrust_n"),
                "thrust_magnitude_n": event.get("thrust_magnitude_n"),
                "available_thrust_n": event.get("available_thrust_n"),
                "mass_flow_rate_kg_s": event.get("mass_flow_rate_kg_s"),
                "active_engines": event.get("active_engines"),
                "active_power_kw": event.get("active_power_kw"),
                "available_power_kw": event.get("available_power_kw"),
            }
        )
    frame, central_body = _output_frame(mission_output)
    return {
        "solution_id": solution_id,
        "detail": "events",
        "frame": frame,
        "central_body": central_body,
        "time_system": "TDB",
        "samples": samples,
        "original_count": len(samples),
        "returned_count": len(samples),
        "materialization_status": "native_events",
    }


def _solution_summary(
    mission_output: Path,
    *,
    parsed: Any,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    metrics = dict(parsed.metrics)
    events = [
        item for item in metrics.get("mission_events", ())
        if isinstance(item, Mapping)
    ]
    start_body = events[0].get("location") if events else None
    end_body = events[-1].get("location") if events else None
    sequence = [
        str(item.get("location"))
        for item in events
        if item.get("location")
    ]
    unique_sequence = [
        value for index, value in enumerate(sequence)
        if index == 0 or value != sequence[index - 1]
    ]
    feasible = bool(parsed.feasible)
    status = str(
        metadata.get("status")
        or ("feasible" if feasible else "output_incomplete" if not parsed.complete else "infeasible")
    )
    summary = {
        "evaluation_key": metadata.get("evaluation_key"),
        "candidate_id": metadata.get("candidate_id"),
        "job_id": metadata.get("job_id"),
        "target_campaign_id": metadata.get("target_campaign_id"),
        "status": status,
        "feasible": feasible,
        "fidelity": metadata.get("fidelity"),
        "objective": parsed.objective,
        "solver_violation": parsed.violation,
        "start_body": start_body,
        "end_body": end_body,
        "sequence_text": " → ".join(unique_sequence) if unique_sequence else mission_output.stem,
        "launch_mjd": metrics.get("launch_epoch"),
        "flight_time_days": metrics.get("flight_time"),
        "propellant_used_kg": metrics.get("total_propellant_used"),
        "delivered_mass_kg": metrics.get("delivered_mass"),
        "deterministic_delta_v_km_s": metrics.get("deterministic_delta_v"),
        "departure_c3_realized_km2_s2": metrics.get("departure_c3"),
        "thrust_min_n": metrics.get("thrust_min"),
        "thrust_max_n": metrics.get("thrust_max"),
        "duty_cycle": metrics.get("thruster_duty_cycle"),
        "active_engines": metrics.get("number_of_thrusters"),
        "bus_power_kw": metrics.get("bus_power"),
        "failure_reason": parsed.failure_reason,
        "metrics": metrics,
    }
    return {key: value for key, value in summary.items() if value is not None}


def create_solution_package(
    mission_output: str | Path,
    destination: str | Path | None = None,
    *,
    artifacts: Mapping[str, str | Path] | None = None,
    metadata: Mapping[str, Any] | None = None,
    producer_version: str = "9",
) -> Path:
    """Create one immutable, portable DeepSpace package from an EMTG output.

    This function performs no network or storage writes. The resulting `.dspkg`
    is the stable handoff boundary for DeepSpace Storage or another consumer.
    """
    source = Path(mission_output).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output = Path(destination or source.with_suffix(".dspkg")).resolve()
    metadata_value = dict(metadata or {})
    failure_file = source.name.upper().startswith("FAILURE_")
    parsed = EMTGResultParser().parse(source, failure_file=failure_file)
    summary = _solution_summary(
        source, parsed=parsed, metadata=metadata_value
    )
    solution_key = str(
        summary.get("evaluation_key")
        or summary.get("candidate_id")
        or _sha256_file(source)
    )
    trajectory = _trajectory_document(solution_key, parsed.metrics, source)
    artifact_sources: dict[str, Path] = {"emtg-output": source}
    for original_role, path_value in (artifacts or {}).items():
        path = Path(path_value).resolve()
        if path.is_file() and path not in {source, output}:
            artifact_sources.setdefault(_safe_role(str(original_role)), path)
    generated = {
        "solution-summary": (
            "generated/solution.json",
            _canonical_json(summary) + b"\n",
            "application/json",
        ),
        "trajectory": (
            "generated/trajectory.json",
            _canonical_json(trajectory) + b"\n",
            "application/json",
        ),
    }
    declared: list[dict[str, Any]] = []
    archive_entries: list[tuple[str, Path | bytes]] = []
    used_paths: set[str] = set()
    for index, (role, path) in enumerate(sorted(artifact_sources.items())):
        safe_role = _safe_role(role)
        package_path = f"artifacts/{index:03d}-{path.name}"
        while package_path in used_paths:
            package_path = f"artifacts/{index:03d}-{safe_role}-{path.name}"
        used_paths.add(package_path)
        declared.append(
            {
                "role": safe_role,
                "path": package_path,
                "sha256": _sha256_file(path),
                "size_bytes": path.stat().st_size,
                "media_type": mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
            }
        )
        archive_entries.append((package_path, path))
    for role, (package_path, content, media_type) in generated.items():
        declared.append(
            {
                "role": role,
                "path": package_path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "media_type": media_type,
            }
        )
        archive_entries.append((package_path, content))
    manifest_metadata: dict[str, Any] = {
        "solution": summary,
        "provenance": {
            **metadata_value,
            "source_filename": source.name,
            "source_sha256": _sha256_file(source),
            "exporter": "PyEMTG.Solution/v1",
        },
    }
    family_metadata = metadata_value.get("family")
    if isinstance(family_metadata, Mapping):
        # Family routing is part of the cross-service package contract, not
        # merely diagnostic exporter provenance.
        manifest_metadata["family"] = dict(family_metadata)
    manifest: dict[str, Any] = {
        "schema": CONTRACT_SCHEMA,
        "schema_version": CONTRACT_VERSION,
        "package_id": "",
        "kind": EMTG_SOLUTION_KIND,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "producer": {"name": "EMTG", "version": str(producer_version)},
        "metadata": manifest_metadata,
        "tags": [
            value
            for value in (
                "emtg",
                "feasible" if parsed.feasible else "infeasible",
                str(summary.get("fidelity") or ""),
            )
            if value
        ],
        "artifacts": declared,
    }
    identity_payload = dict(manifest)
    identity_payload.pop("package_id")
    identity_payload.pop("created_at")
    manifest["package_id"] = "ds1_" + hashlib.sha256(
        _canonical_json(identity_payload)
    ).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(descriptor)
    try:
        with zipfile.ZipFile(
            temporary_name, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("manifest.json", _canonical_json(manifest) + b"\n")
            for package_path, value in archive_entries:
                if isinstance(value, Path):
                    archive.write(value, package_path)
                else:
                    archive.writestr(package_path, value)
        os.replace(temporary_name, output)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    return output
