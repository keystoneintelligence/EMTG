"""Discover NLP solvers compiled into the EMTG executable used by PyEMTG."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Mapping


SOLVER_NAMES = {0: "SNOPT", 2: "IPOPT"}


def _parse_environment(value: str) -> list[int]:
    aliases = {"0": 0, "snopt": 0, "2": 2, "ipopt": 2}
    parsed: list[int] = []
    for entry in value.split(","):
        solver_type = aliases.get(entry.strip().lower())
        if solver_type is not None and solver_type not in parsed:
            parsed.append(solver_type)
    return parsed


def discover_solver_types(
    capability_file: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[int]:
    """Return selectable solver codes, preserving EMTG's non-contiguous IDs.

    The build writes ``bin/solver_capabilities.json`` next to EMTGv9. Packaged
    or remote front ends can instead set ``EMTG_AVAILABLE_NLP_SOLVERS`` to a
    comma-separated list such as ``ipopt`` or ``0,2``.
    """

    environment = os.environ if environ is None else environ
    configured = environment.get("EMTG_AVAILABLE_NLP_SOLVERS")
    if configured is not None:
        return _parse_environment(configured)

    candidates: list[Path] = []
    if capability_file is not None:
        candidates.append(Path(capability_file))
    elif environment.get("EMTG_SOLVER_CAPABILITIES"):
        candidates.append(Path(environment["EMTG_SOLVER_CAPABILITIES"]))
    else:
        candidates.append(Path(__file__).resolve().parents[1] / "bin" / "solver_capabilities.json")

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            capabilities = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        available = []
        if capabilities.get("snopt") is True:
            available.append(0)
        if capabilities.get("ipopt") is True:
            available.append(2)
        return available

    # No executable has been built or selected yet. Show EMTG's supported
    # backends, while excluding the reserved/unsupported WORHP value.
    return [0, 2]


def available_solver_choices(**kwargs) -> list[tuple[int, str]]:
    return [(solver_type, SOLVER_NAMES[solver_type]) for solver_type in discover_solver_types(**kwargs)]
