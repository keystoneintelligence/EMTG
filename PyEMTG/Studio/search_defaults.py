from __future__ import annotations

from pathlib import Path
from typing import Any

from .search_effort import PRODUCTION_SEARCH_EFFORT, apply_search_effort


def _runtime_roots(workspace: Path, bundled_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for start in (workspace, bundled_root):
        for candidate in (start, *start.parents):
            resolved = candidate.resolve()
            if resolved not in candidates:
                candidates.append(resolved)
    return candidates


def _find_runtime_root(workspace: Path, bundled_root: Path) -> Path:
    for root in _runtime_roots(workspace, bundled_root):
        if (root / "bin" / "EMTGv9.exe").is_file() and (root / "testatron" / "universe").is_dir():
            return root
    return workspace


def default_search_configuration(workspace: Path, bundled_root: Path) -> dict[str, Any]:
    """Return a real, bounded EMTG campaign rooted in discovered local assets."""
    runtime = _find_runtime_root(workspace, bundled_root)
    asteroid_case = (
        runtime / "testatron" / "tests" / "integration_asteroid_missions"
        / "A20136163_AEPS_IPOPT_FBLT.emtgopt"
    )
    mars_case = runtime / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt"
    use_asteroid = asteroid_case.is_file()
    base_case = asteroid_case if use_asteroid else mars_case
    final_body = "A20136163" if use_asteroid else "Mars"
    phase_type = 3 if use_asteroid else 6
    arrival_type = 3 if use_asteroid else 2
    launch_lower, launch_upper = (61200, 61300) if use_asteroid else (53701, 53711)
    flight_lower, flight_upper = (700, 1600) if use_asteroid else (120, 360)

    config = {
        "schema_version": "outerloop/v2",
        "base_case": str(base_case.resolve()),
        "run_directory": "_local/studio/managed-by-studio",
        "root_seed": 20260712,
        "assets": {
            "executable": str((runtime / "bin" / "EMTGv9.exe").resolve()),
            "universe_folder": str((runtime / "testatron" / "universe").resolve()),
            "hardware_path": str((runtime / "testatron" / "HardwareModels").resolve()),
            "capabilities_file": str((runtime / "bin" / "solver_capabilities.json").resolve()),
        },
        "search": {
            "max_journeys": 1,
            "min_journeys": 1,
            "max_flybys": 0,
            "min_flybys": 0,
            "fixed_start": "Earth",
            "fixed_final": final_body,
            "repairs": [],
            "mission_genes": {
                "launch_window_open_date": {"kind": "integer", "lower": launch_lower, "upper": launch_upper},
                "flight_time": {"kind": "integer", "lower": flight_lower, "upper": flight_upper},
            },
            "journey_genes": {
                "phase_type": {"kind": "fixed", "value": phase_type},
                "arrival_type": {"kind": "fixed", "value": arrival_type},
            },
            "phase_genes": {},
            "flyby_bodies": [],
        },
        "objectives": ["flight_time", "delivered_mass"],
        "algorithm": {
            "population_size": 20,
            "generations": 4,
            "tournament_size": 2,
            "crossover_probability": 0.9,
            "mutation_probability": 0.7,
            "stall_generations": 4,
            "trials": 1,
        },
        "evaluator": {
            "type": "emtg",
            "timeout_seconds": 720,
            "check_ephemeris_coverage": False,
            "ephemeris_source_override": 1,
            "supported_phase_types": [phase_type],
            "budget": {
                "inner_loop": "mbh",
                "mbh_max_run_time": 600,
                "mbh_max_trials": 200000,
                "nlp_solver_type": 2,
                "nlp_max_run_time": 600,
                "nlp_major_iterations": 5000,
                "quiet_nlp": 1,
            },
        },
        "workers": {"count": 10, "infrastructure_retries": 1},
        "checkpoint_every": 2,
    }
    apply_search_effort(config, PRODUCTION_SEARCH_EFFORT)
    required = {
        "EMTG executable": Path(config["assets"]["executable"]),
        "base mission": base_case,
        "universe folder": Path(config["assets"]["universe_folder"]),
        "hardware folder": Path(config["assets"]["hardware_path"]),
        "solver capabilities": Path(config["assets"]["capabilities_file"]),
    }
    missing = [f"{name}: {path}" for name, path in required.items() if not path.exists()]
    return {
        "config": config,
        "ready": not missing,
        "missing": missing,
        "runtime_root": str(runtime),
        "template": "Earth to A20136163" if use_asteroid else "Earth to Mars",
    }
