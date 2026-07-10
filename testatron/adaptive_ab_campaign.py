"""Run reproducible paired fixed/adaptive EMTG optimization campaigns.

The runner changes only the integration mode and per-run output directory inside
each pair. It alternates execution order, records exact seeds/configuration, and
writes JSON plus CSV artifacts suitable for smoke, local, and nightly tiers.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
PYEMTG = REPO_ROOT / "PyEMTG"
DEFAULT_EMTG = REPO_ROOT / "bin" / "EMTGv9.exe"
DEFAULT_CASE = (
    REPO_ROOT
    / "testatron"
    / "tests"
    / "integration_asteroid_missions"
    / "A20136163_AEPS_IPOPT_FBLT.emtgopt"
)
TIER_SEEDS = {
    "smoke": [104729],
    "local": [104729, 130363, 155921, 181081, 206369],
}


@dataclass
class RunResult:
    mode: str
    seed: int
    order: int
    return_code: int
    wall_seconds: float
    output_directory: str
    options_file: str
    mission_file: str | None = None
    failure_file: str | None = None
    feasible: bool = False
    objective: float | None = None
    normalized_feasibility: float | None = None
    final_mass_kg: float | None = None
    flight_time_years: float | None = None
    time_to_first_feasible_seconds: float | None = None
    solution_family: str | None = None
    failure_category: str | None = None


def load_pyemtg():
    if str(PYEMTG) not in sys.path:
        sys.path.insert(0, str(PYEMTG))
    import Mission  # type: ignore
    import MissionOptions  # type: ignore

    return Mission, MissionOptions


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_option_lines(path: Path, ignored_keys: set[str]) -> list[str]:
    canonical = []
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line in {"BEGIN_TRIALX", "END_TRIALX"}:
            continue
        key = line.split(maxsplit=1)[0]
        if key not in ignored_keys:
            canonical.append(line)
    return canonical


def assert_pair_is_controlled(fixed_options: Path, adaptive_options: Path) -> None:
    ignored = {"integratorType", "forced_working_directory"}
    if canonical_option_lines(fixed_options, ignored) != canonical_option_lines(adaptive_options, ignored):
        raise AssertionError(
            "Paired options differ outside integratorType and forced_working_directory."
        )


def prepare_run_options(case_file: Path,
                        output_dir: Path,
                        mode: str,
                        seed: int,
                        run_budget_seconds: int | None = None) -> Path:
    _, MissionOptions = load_pyemtg()
    options = MissionOptions.MissionOptions(str(case_file))
    options.integratorType = 1 if mode == "fixed" else 0
    options.MBH_RNG_seed = seed
    if run_budget_seconds is not None:
        options.MBH_max_run_time = run_budget_seconds
        options.snopt_max_run_time = run_budget_seconds
    options.override_working_directory = 1
    options.forced_working_directory = output_dir.as_posix()
    options.override_mission_subfolder = 1
    options.forced_mission_subfolder = "."
    options.short_output_file_names = 1
    options.background_mode = 1
    options.universe_folder = (REPO_ROOT / "testatron" / "universe").as_posix()
    options.HardwarePath = (REPO_ROOT / "testatron" / "HardwareModels").as_posix()
    output_dir.mkdir(parents=True, exist_ok=False)
    run_options = output_dir / case_file.name
    options.write_options_file(str(run_options), writeAll=True)
    return run_options


def quantize(value: float, width: float) -> int:
    return int(round(value / width))


def solution_family_signature(mission) -> str:
    """Deterministic coarse basin signature from boundary and encounter geometry."""

    features: list[object] = [
        quantize(float(mission.objective_value), 1.0e-4),
        quantize(float(mission.total_flight_time_years), 1.0e-3),
        quantize(float(mission.final_mass_including_propellant_margin), 1.0),
    ]
    boundary_types = {
        "launch", "departure", "pwr_flyby", "upwr_flyby", "LT_rndzvs",
        "intercept", "rendezvous", "insertion", "entry_interface",
    }
    for journey in mission.Journeys:
        for event in journey.missionevents:
            if event.EventType not in boundary_types and "flyby" not in event.EventType:
                continue
            features.extend(
                [
                    event.EventType,
                    event.Location,
                    quantize(float(event.JulianDate), 0.25),
                    quantize(float(event.Altitude), 10.0),
                    quantize(float(event.Mass), 1.0),
                ]
            )
            features.extend(quantize(float(component), 1000.0) for component in event.SpacecraftState[:3])
            features.extend(quantize(float(component), 0.01) for component in event.SpacecraftState[3:])
    encoded = json.dumps(features, separators=(",", ":"), sort_keys=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def parse_run_output(result: RunResult, mission_name: str) -> RunResult:
    Mission, _ = load_pyemtg()
    output_dir = Path(result.output_directory)
    success = output_dir / f"{mission_name}.emtg"
    failure = output_dir / f"FAILURE_{mission_name}.emtg"
    selected = success if success.exists() else failure if failure.exists() else None
    if selected is None:
        log_file = output_dir / "emtg.log"
        log_text = log_file.read_text(encoding="utf-8", errors="replace") if log_file.exists() else ""
        if "invalid decision-variable bounds" in log_text:
            result.failure_category = "invalid_bounds_before_propagation"
        elif "Failure to run inner-loop solver" in log_text:
            result.failure_category = "inner_loop_setup_failure"
        else:
            result.failure_category = "no_mission_output"
        return result
    result.mission_file = str(selected)
    is_failure_output = selected == failure
    if is_failure_output:
        result.failure_file = str(selected)

    try:
        mission = Mission.Mission(str(selected))
        result.objective = float(mission.objective_value)
        result.normalized_feasibility = abs(float(mission.worst_violation))
        result.final_mass_kg = float(mission.final_mass_including_propellant_margin)
        result.flight_time_years = float(mission.total_flight_time_years)
        result.time_to_first_feasible_seconds = float(
            mission.timeToCompletionOfBestSolutionAttempt
        ) if hasattr(mission, "timeToCompletionOfBestSolutionAttempt") else None
        result.feasible = bool(mission.first_nlp_solve_feasible) and not is_failure_output
        if result.feasible:
            result.solution_family = solution_family_signature(mission)
        else:
            result.failure_category = "infeasible_output" if is_failure_output else "reported_infeasible"
    except Exception as error:  # preserve campaign evidence even for a malformed output
        result.failure_category = f"parse_error:{type(error).__name__}"
    return result


def read_mission_name(options_file: Path) -> str:
    for line in options_file.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] == "mission_name":
            return fields[1]
    raise ValueError(f"mission_name not found in {options_file}")


def execute_run(emtg: Path, options_file: Path, mode: str, seed: int, order: int, timeout: int) -> RunResult:
    output_dir = options_file.parent
    log_file = output_dir / "emtg.log"
    start = time.perf_counter()
    environment = os.environ.copy()
    environment["PATH"] = os.pathsep.join(
        [str(emtg.parent), str(REPO_ROOT / "bin"), environment.get("PATH", "")]
    )
    try:
        with log_file.open("w", encoding="utf-8") as log:
            completed = subprocess.run(
                [str(emtg), str(options_file)],
                stdout=log,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
                env=environment,
            )
        return_code = completed.returncode
        failure_category = None if return_code == 0 else f"exit_code_{return_code}"
    except subprocess.TimeoutExpired:
        return_code = -1
        failure_category = "timeout"
    result = RunResult(
        mode=mode,
        seed=seed,
        order=order,
        return_code=return_code,
        wall_seconds=time.perf_counter() - start,
        output_directory=str(output_dir),
        options_file=str(options_file),
        failure_category=failure_category,
    )
    if return_code == 0:
        return parse_run_output(result, read_mission_name(options_file))
    return result


def summarize(results: list[RunResult]) -> dict[str, object]:
    summary: dict[str, object] = {}
    family_sets: dict[str, set[str]] = {}
    for mode in ("fixed", "adaptive"):
        selected = [result for result in results if result.mode == mode]
        feasible = [result for result in selected if result.feasible]
        runtimes = [result.wall_seconds for result in selected]
        objectives = [result.objective for result in feasible if result.objective is not None]
        families = {result.solution_family for result in feasible if result.solution_family}
        family_sets[mode] = families
        summary[mode] = {
            "runs": len(selected),
            "feasible_runs": len(feasible),
            "feasible_rate": len(feasible) / len(selected) if selected else 0.0,
            "runtime_median_seconds": statistics.median(runtimes) if runtimes else None,
            "runtime_stdev_seconds": statistics.stdev(runtimes) if len(runtimes) > 1 else 0.0,
            "best_objective": min(objectives) if objectives else None,
            "objective_median": statistics.median(objectives) if objectives else None,
            "distinct_solution_families": len(families),
            "failure_categories": sorted(result.failure_category for result in selected if result.failure_category),
        }
    summary["family_comparison"] = {
        "shared": sorted(family_sets["fixed"] & family_sets["adaptive"]),
        "fixed_only": sorted(family_sets["fixed"] - family_sets["adaptive"]),
        "adaptive_only": sorted(family_sets["adaptive"] - family_sets["fixed"]),
    }
    return summary


def write_artifacts(root: Path, metadata: dict[str, object], results: list[RunResult]) -> None:
    payload = {"metadata": metadata, "runs": [asdict(result) for result in results], "summary": summarize(results)}
    (root / "campaign.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    with (root / "runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(asdict(results[0]).keys()))
        writer.writeheader()
        writer.writerows(asdict(result) for result in results)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--emtg", type=Path, default=DEFAULT_EMTG)
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--tier", choices=("smoke", "local", "nightly"), default="smoke")
    parser.add_argument("--nightly-seed-count", type=int, default=30)
    parser.add_argument("--output-root", type=Path, default=REPO_ROOT / "_local" / "adaptive_ab")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--run-budget-seconds", type=int, default=None,
                        help="Override the equal EMTG/MBH wall-time budget in both modes.")
    parser.add_argument("--reparse-campaign", type=Path, default=None,
                        help="Re-parse preserved run outputs and refresh JSON/CSV without rerunning EMTG.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.reparse_campaign:
        campaign_root = args.reparse_campaign.resolve()
        payload = json.loads((campaign_root / "campaign.json").read_text(encoding="utf-8"))
        results = [RunResult(**record) for record in payload["runs"]]
        reparsed = []
        for result in results:
            if result.return_code == 0:
                result.failure_category = None
                reparsed.append(parse_run_output(result, read_mission_name(Path(result.options_file))))
            else:
                reparsed.append(result)
        write_artifacts(campaign_root, payload["metadata"], reparsed)
        return 0

    emtg = args.emtg.resolve()
    case_file = args.case.resolve()
    if not emtg.is_file() or not case_file.is_file():
        raise FileNotFoundError(f"Missing executable or case: {emtg}, {case_file}")
    seeds = TIER_SEEDS.get(args.tier) or list(range(104729, 104729 + args.nightly_seed_count))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    campaign_root = args.output_root.resolve() / f"{case_file.stem}_{args.tier}_{stamp}"
    campaign_root.mkdir(parents=True, exist_ok=False)
    metadata = {
        "tier": args.tier,
        "case": str(case_file),
        "case_sha256": file_sha256(case_file),
        "emtg": str(emtg),
        "emtg_sha256": file_sha256(emtg),
        "seeds": seeds,
        "run_budget_seconds": args.run_budget_seconds,
        "platform": platform.platform(),
        "processor": platform.processor(),
        "python": platform.python_version(),
        "started_utc": stamp,
    }

    results: list[RunResult] = []
    for pair_index, seed in enumerate(seeds):
        pair_root = campaign_root / f"seed_{seed}"
        fixed_options = prepare_run_options(
            case_file, pair_root / "fixed", "fixed", seed, args.run_budget_seconds)
        adaptive_options = prepare_run_options(
            case_file, pair_root / "adaptive", "adaptive", seed, args.run_budget_seconds)
        assert_pair_is_controlled(fixed_options, adaptive_options)
        order = ("fixed", "adaptive") if pair_index % 2 == 0 else ("adaptive", "fixed")
        options_by_mode = {"fixed": fixed_options, "adaptive": adaptive_options}
        for order_index, mode in enumerate(order):
            results.append(execute_run(emtg, options_by_mode[mode], mode, seed, order_index, args.timeout))
            write_artifacts(campaign_root, metadata, results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
