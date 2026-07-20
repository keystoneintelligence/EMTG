from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, MutableMapping


SMOKE_SEARCH_EFFORT = {
    "id": "smoke",
    "name": "Smoke test",
    "description": "Fast plumbing check; not intended to find difficult mission solutions.",
    "parallel_candidates": 2,
    "population_size": 4,
    "generations": 2,
    "stall_generations": 2,
    "trials": 1,
    "solve_time_seconds": 30,
    "nlp_major_iterations": 500,
    "mbh_max_trials": 500,
    "watchdog_seconds": 120,
}


PRODUCTION_SEARCH_EFFORT = {
    "id": "production",
    "name": "Production",
    "description": "Balanced asteroid-search budget sized to keep ten local EMTG workers busy.",
    "parallel_candidates": 10,
    "population_size": 20,
    "generations": 4,
    "stall_generations": 4,
    "trials": 1,
    "solve_time_seconds": 600,
    "nlp_major_iterations": 5000,
    "mbh_max_trials": 200000,
    "watchdog_seconds": 720,
}


def default_search_effort_presets() -> dict[str, Any]:
    return {
        "default_id": "production",
        "items": [deepcopy(SMOKE_SEARCH_EFFORT), deepcopy(PRODUCTION_SEARCH_EFFORT)],
    }


def apply_search_effort(
    config: MutableMapping[str, Any], preset: Mapping[str, Any]
) -> MutableMapping[str, Any]:
    """Apply one Studio search-effort preset without replacing unrelated campaign options."""
    algorithm = dict(config.get("algorithm", {}))
    algorithm.update({
        "population_size": int(preset["population_size"]),
        "generations": int(preset["generations"]),
        "stall_generations": int(preset["stall_generations"]),
        "trials": int(preset["trials"]),
    })
    config["algorithm"] = algorithm

    evaluator = dict(config.get("evaluator", {}))
    evaluator["timeout_seconds"] = int(preset["watchdog_seconds"])
    budget = dict(evaluator.get("budget", {}))
    budget.update({
        "inner_loop": "mbh",
        "mbh_max_run_time": int(preset["solve_time_seconds"]),
        "mbh_max_trials": int(preset["mbh_max_trials"]),
        "nlp_max_run_time": int(preset["solve_time_seconds"]),
        "nlp_major_iterations": int(preset["nlp_major_iterations"]),
    })
    evaluator["budget"] = budget
    config["evaluator"] = evaluator

    workers = dict(config.get("workers", {}))
    workers["count"] = int(preset["parallel_candidates"])
    config["workers"] = workers
    return config
