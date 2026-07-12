from __future__ import annotations

import ast
import csv
from pathlib import Path
import re
from typing import Any

from .models import OptionField


ENUM_PATTERN = re.compile(r"^#(\d+):?\s*(.+)$", re.MULTILINE)


def _literal(value: str) -> Any:
    text = value.strip()
    if not text:
        return None
    if text in {"inf", "+inf"}:
        return None
    if text == "minf":
        return None
    try:
        return ast.literal_eval(text)
    except (SyntaxError, ValueError):
        try:
            return float(text) if "." in text or "e" in text.lower() else int(text)
        except ValueError:
            return text


def _group(name: str, scope: str) -> str:
    if scope == "journey":
        return "Journey"
    lowered = name.lower()
    if lowered.startswith(("mbh_", "nlp_", "snopt_")) or "solver" in lowered:
        return "Solver"
    if any(token in lowered for token in ("ephemeris", "output", "file", "working_directory")):
        return "Output"
    if any(token in lowered for token in ("propagat", "integrat", "gravity", "spice", "perturb")):
        return "Physics"
    if any(token in lowered for token in ("spacecraft", "power", "propulsion", "thruster", "launch_vehicle", "tank")):
        return "Spacecraft"
    return "Global"


def load_option_schema(repository_root: str | Path) -> list[OptionField]:
    root = Path(repository_root)
    output: list[OptionField] = []
    for scope, relative in (
        ("mission", "OptionsOverhaul/list_of_missionoptions.csv"),
        ("journey", "OptionsOverhaul/list_of_journeyoptions.csv"),
    ):
        with (root / relative).open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                description = (row.get("description") or row.get("comment") or "").replace("\\n", "\n")
                choices = [
                    {"value": int(value), "label": label.strip().rstrip(".")}
                    for value, label in ENUM_PATTERN.findall(description)
                ]
                output.append(OptionField(
                    scope=scope,
                    group=_group(name, scope),
                    name=name,
                    data_type=(row.get("dataType") or "string").strip(),
                    default=_literal(row.get("defaultValue") or ""),
                    lower=_literal(row.get("lowerBound") or ""),
                    upper=_literal(row.get("upperBound") or ""),
                    description=description,
                    choices=choices,
                ))
    return output
