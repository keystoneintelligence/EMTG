"""Canonical OuterLoop gene names and compatibility aliases."""

from __future__ import annotations

from typing import Any, Mapping


MISSION_GENE_ALIASES = {
    "launch_epoch": "launch_window_open_date",
}


def canonical_mission_gene_name(name: str) -> str:
    return MISSION_GENE_ALIASES.get(name, name)


def canonicalize_mission_genes(values: Mapping[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, value in values.items():
        canonical = canonical_mission_gene_name(str(name))
        if canonical in output and output[canonical] != value:
            raise ValueError(f"conflicting mission gene values for {canonical}")
        output[canonical] = value
    return output


def schema3_identity_mission_genes(values: Mapping[str, Any]) -> dict[str, Any]:
    """Retain schema-3 candidate hashes across compatibility-only renames."""
    output = canonicalize_mission_genes(values)
    if "launch_window_open_date" in output:
        output["launch_epoch"] = output.pop("launch_window_open_date")
    return output
