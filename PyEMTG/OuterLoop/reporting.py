"""Versioned JSONL/CSV reporting and candidate promotion."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .legacy import write_legacy_nsgaii
from .serde import candidate_to_dict, result_to_dict
from .storage import CampaignStore, atomic_write_text
from .canonical import file_sha256, source_manifest
from .storage import atomic_write_json


def _matches(record: Mapping[str, Any], filters: Mapping[str, str] | None) -> bool:
    if not filters:
        return True
    candidate = record.get("candidate")
    result = record.get("result")
    for key, expected in filters.items():
        if key == "feasibility":
            actual = "feasible" if result and result.feasible else "infeasible"
        elif key == "status":
            actual = result.status.value if result else "pending"
        elif key == "body":
            actual = candidate.phenotype.sequence_text if candidate else ""
            if expected not in actual:
                return False
            continue
        elif key == "hardware":
            actual = json.dumps(candidate.phenotype.mission, sort_keys=True) if candidate else ""
            if expected not in actual:
                return False
            continue
        elif key == "group":
            groups = candidate.phenotype.point_group if candidate else {}
            if expected not in groups:
                return False
            continue
        elif key == "objective":
            parts = expected.split(":")
            name = parts[0]
            value = result.metrics.get(name) if result else None
            if not isinstance(value, (int, float)):
                return False
            if len(parts) > 1 and parts[1] and float(value) < float(parts[1]):
                return False
            if len(parts) > 2 and parts[2] and float(value) > float(parts[2]):
                return False
            continue
        elif key == "trial":
            actual = str(record.get("trial", candidate.trial if candidate else ""))
        elif key == "context":
            actual = str(record.get("comparison_context_id", ""))
        else:
            raise ValueError(f"unknown export filter {key}")
        if actual != expected:
            return False
    return True


def _record_dict(record: Mapping[str, Any]) -> dict[str, Any]:
    candidate = record.get("candidate")
    result = record.get("result")
    return {
        "trial": record.get("trial", candidate.trial if candidate else None),
        "generation": record.get("generation"),
        "role": record.get("role"),
        "position": record.get("position"),
        "fidelity": record.get("fidelity", result.fidelity if result else None),
        "comparison_context_id": record.get("comparison_context_id"),
        "objectives": list(record.get("objectives", ())),
        "candidate": candidate_to_dict(candidate) if candidate else None,
        "result": result_to_dict(result) if result else None,
    }


def export_run(
    run_directory: str | Path,
    output_directory: str | Path | None = None,
    *,
    filters: Mapping[str, str] | None = None,
    legacy: bool = True,
    fidelity: str | None = None,
) -> dict[str, str]:
    store = CampaignStore(run_directory)
    resolved = store.get_metadata("resolved_configuration", {})
    configured_output = resolved.get("outputs", {}).get("directory")
    output = Path(output_directory or configured_output or (Path(run_directory) / "exports")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    ladder = sorted(resolved.get("fidelities", ()), key=lambda value: int(value.get("rank", 0)))
    if fidelity is None and ladder:
        fidelity = "confirmed" if any(value.get("name") == "confirmed" for value in ladder) else str(ladder[-1]["name"])
    if fidelity not in {None, "all"}:
        known = {str(value.get("name")) for value in ladder} or {record["fidelity"] for record in store.archive_records()}
        if fidelity not in known:
            raise ValueError(f"unknown export fidelity {fidelity}")
    selected_fidelity = None if fidelity in {None, "all"} else fidelity
    generation_records = [record for record in store.generation_records() if _matches(record, filters)]
    archive_records = [record for record in store.archive_records(selected_fidelity) if _matches(record, filters)]
    evaluation_records = [record for record in store.evaluation_records(selected_fidelity) if _matches(record, filters)]

    all_path = output / "all-evaluations.jsonl"
    unique: dict[str, Mapping[str, Any]] = {}
    for record in evaluation_records:
        result = record.get("result")
        key = result.evaluation_key if result else record["candidate"].individual_id
        unique[f"{key}:{len(unique)}"] = record
    atomic_write_text(all_path, "".join(json.dumps(_record_dict(record), sort_keys=True, allow_nan=False) + "\n" for record in unique.values()))

    pareto_path = output / "pareto-front.jsonl"
    atomic_write_text(pareto_path, "".join(json.dumps(_record_dict(record), sort_keys=True, allow_nan=False) + "\n" for record in archive_records))

    csv_path = output / "all-evaluations.csv"
    metric_names = sorted({name for record in unique.values() if record.get("result") for name in record["result"].metrics if isinstance(record["result"].metrics[name], (int, float))})
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        headers = ["candidate_id", "individual_id", "trial", "generation", "role", "status", "fidelity", "comparison_context_id", "runtime_seconds", "sequence", *metric_names]
        writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for record in unique.values():
            candidate, result = record.get("candidate"), record.get("result")
            row = {
                "candidate_id": candidate.candidate_id if candidate else result.candidate_id,
                "individual_id": candidate.individual_id if candidate else "",
                "trial": candidate.trial if candidate else "",
                "generation": record.get("generation"),
                "role": record.get("role"),
                "status": result.status.value if result else "pending",
                "fidelity": result.fidelity if result else "",
                "comparison_context_id": record.get("comparison_context_id", ""),
                "runtime_seconds": result.runtime_seconds if result else "",
                "sequence": candidate.phenotype.sequence_text if candidate else "",
            }
            if result:
                row.update({name: result.metrics.get(name, "") for name in metric_names})
            writer.writerow(row)

    history_path = output / "convergence-history.json"
    atomic_write_text(history_path, json.dumps(store.metadata_items("stall_"), sort_keys=True, indent=2, allow_nan=False) + "\n")
    populations_path = output / "population-history.csv"
    with populations_path.open("w", encoding="utf-8", newline="") as stream:
        headers = [
            "trial", "generation", "role", "position", "individual_id", "candidate_id",
            "status", "fidelity", "sequence", "parents", "operators",
        ]
        writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
        writer.writeheader()
        for record in generation_records:
            candidate, result = record["candidate"], record.get("result")
            writer.writerow({
                "trial": record["trial"],
                "generation": record["generation"],
                "role": record["role"],
                "position": record["position"],
                "individual_id": candidate.individual_id,
                "candidate_id": candidate.candidate_id,
                "status": result.status.value if result else "pending",
                "fidelity": result.fidelity if result else "",
                "sequence": candidate.phenotype.sequence_text,
                "parents": ";".join(candidate.parents),
                "operators": ";".join(candidate.operators),
            })
    paths = {
        "all_jsonl": str(all_path),
        "pareto_jsonl": str(pareto_path),
        "all_csv": str(csv_path),
        "population_csv": str(populations_path),
        "history": str(history_path),
    }
    if legacy:
        objective_names = [value["name"] if isinstance(value, dict) else value for value in resolved.get("objectives", [])]
        legacy_records = [
            {
                "generation": record.get("generation", 0),
                "output_file": record["result"].artifacts.get("emtg", "") if record.get("result") else "",
                "description": f"{record['candidate'].candidate_id} ({record['candidate'].phenotype.sequence_text})",
                "metrics": record["result"].metrics if record.get("result") else {},
            }
            for record in archive_records
            if record.get("candidate")
        ]
        legacy_path = output / "pareto.NSGAII"
        write_legacy_nsgaii(legacy_path, legacy_records, objective_names)
        paths["legacy"] = str(legacy_path)
    return paths


def inspect_candidate(run_directory: str | Path, identifier: str) -> dict[str, Any]:
    found = CampaignStore(run_directory).find_candidate(identifier)
    if found is None:
        raise KeyError(f"candidate not found: {identifier}")
    candidate, result = found
    return {
        "candidate": candidate_to_dict(candidate),
        "result": result_to_dict(result) if result else None,
    }


def promote_candidate(
    run_directory: str | Path,
    identifier: str,
    output_path: str | Path | None = None,
    *,
    allow_stale_context: bool = False,
) -> Path:
    inspected = inspect_candidate(run_directory, identifier)
    result = inspected["result"]
    if not result or result["status"] != "feasible":
        raise ValueError("only a feasible, completed candidate can be promoted")
    store = CampaignStore(run_directory)
    recorded_source = store.get_metadata("source_manifest", {})
    current_source = source_manifest(Path(__file__).resolve().parents[2])
    stale_reasons = []
    if recorded_source.get("content_hash") != current_source.get("content_hash"):
        stale_reasons.append("OuterLoop/adapter source content changed")
    artifact_hashes = result.get("provenance", {}).get("artifact_hashes", {})
    for role, digest in artifact_hashes.items():
        value = result.get("artifacts", {}).get(role)
        if not value or not Path(value).is_file() or file_sha256(value) != digest:
            stale_reasons.append(f"artifact {role} is missing or changed")
    if stale_reasons and not allow_stale_context:
        raise ValueError(
            "promotion context is stale: " + "; ".join(stale_reasons)
            + "; pass --allow-stale-context to accept non-reproducible provenance"
        )
    source_value = result.get("artifacts", {}).get("options")
    if not source_value or not Path(source_value).is_file():
        raise FileNotFoundError("stored standalone .emtgopt artifact is unavailable")
    descriptions = result.get("metrics", {}).get("xdescriptions")
    vector = result.get("metrics", {}).get("decision_vector")
    if (
        not isinstance(descriptions, list)
        or not isinstance(vector, list)
        or not descriptions
        or len(descriptions) != len(vector)
    ):
        raise ValueError("the result does not contain a complete optimized decision vector")
    target = Path(output_path or (Path(run_directory) / "exports" / f"{identifier[:16]}.emtgopt")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        from ..MissionOptions import MissionOptions
    except ImportError:  # PyEMTG may be placed directly on PYTHONPATH
        from MissionOptions import MissionOptions
    options = MissionOptions(str(source_value))
    if not options.success:
        raise ValueError("the stored case options cannot be parsed")
    options.mission_name = f"promoted_{identifier[:16]}"
    # EMTG's default ``../EMTG_v9_results`` path is not reliable for a case
    # launched from an arbitrary directory.  The promoted file owns an
    # explicit, already-existing output directory just like worker cases do.
    options.override_working_directory = 1
    options.forced_working_directory = str(target.parent).replace("\\", "/")
    options.run_inner_loop = 0
    options.trialX = [
        [str(description), float(value)]
        for description, value in zip(descriptions, vector)
    ]
    options.DisassembleMasterDecisionVector()
    options.write_options_file(str(target))
    atomic_write_json(
        target.with_suffix(target.suffix + ".provenance.json"),
        {
            "schema_version": 3,
            "candidate_id": identifier,
            "evaluation_key": result.get("evaluation_key"),
            "non_reproducible_override": bool(stale_reasons),
            "stale_reasons": stale_reasons,
            "artifact_hashes": artifact_hashes,
        },
    )
    return target


def plot_metrics(
    run_directory: str | Path,
    metrics: Iterable[str],
    output_path: str | Path | None = None,
) -> Path:
    names = tuple(metrics)
    if not 2 <= len(names) <= 5:
        raise ValueError("select between two and five metrics")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as error:
        raise RuntimeError("Matplotlib is required only for plotting") from error
    records = CampaignStore(run_directory).archive_records()
    points = []
    for record in records:
        result = record["result"]
        values = [result.metrics.get(name) for name in names]
        if all(isinstance(value, (int, float)) for value in values):
            points.append([float(value) for value in values])
    if not points:
        raise ValueError("the Pareto archive has no complete rows for the selected metrics")
    figure = plt.figure(figsize=(9, 7))
    if len(names) == 2:
        axes = figure.add_subplot(111)
        axes.scatter([point[0] for point in points], [point[1] for point in points])
    else:
        axes = figure.add_subplot(111, projection="3d")
        colors = [point[3] for point in points] if len(names) >= 4 else "tab:blue"
        sizes = None
        if len(names) == 5:
            fifth = [point[4] for point in points]
            span = max(fifth) - min(fifth)
            sizes = [30.0 + (value - min(fifth)) / span * 120.0 if span else 60.0 for value in fifth]
        plotted = axes.scatter(
            [point[0] for point in points],
            [point[1] for point in points],
            [point[2] for point in points],
            c=colors,
            s=sizes,
        )
        axes.set_zlabel(names[2])
        if len(names) >= 4:
            figure.colorbar(plotted, ax=axes, label=names[3])
    axes.set_xlabel(names[0])
    axes.set_ylabel(names[1])
    axes.set_title("EMTG outer-loop Pareto archive")
    figure.tight_layout()
    target = Path(output_path or (Path(run_directory) / "exports" / "pareto-metrics.png")).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=160)
    plt.close(figure)
    return target
