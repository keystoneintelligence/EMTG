"""Storage-neutral publication payloads for EMTG solution families.

The returned dictionaries are accepted directly by DeepSpace Storage's family
create and family-membership interfaces.  This module deliberately has no
storage client or persistence dependency: callers decide whether to publish
through the DeepSpace HTTP API, CLI, or an in-process store.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

try:
    from ..OuterLoop.atlas import FamilyDefinition, FamilySample
except ImportError:  # Support importing Solution from a PyEMTG sys.path entry.
    from OuterLoop.atlas import FamilyDefinition, FamilySample


def family_resource_payload(
    family: FamilyDefinition,
    *,
    name: str | None = None,
    description: str = "",
    parent_family_id: str | None = None,
    tags: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a JSON-safe DeepSpace family-create payload."""
    family_name = str(name or "").strip()
    if not family_name:
        family_name = f"EMTG feasibility family {family.family_id[4:16]}"
    return {
        "family_id": family.family_id,
        "name": family_name,
        "description": str(description),
        "parent_family_id": parent_family_id,
        "definition": family.to_dict(),
        "axes": [axis.to_dict() for axis in family.axes],
        "tags": sorted(
            {
                str(tag).strip()
                for tag in tags
                if str(tag).strip()
            }
        ),
    }


def family_membership_payload(
    sample: FamilySample,
    *,
    role: str = "sample",
    ordinal: int | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a JSON-safe DeepSpace add-family-member payload."""
    sample_document = sample.to_dict()
    coordinates = {
        str(item["key"]): item["value"]
        for item in sample_document["parameters"]["values"]
    }
    publication_metadata = dict(metadata or {})
    publication_metadata["emtg_family_sample"] = sample_document
    return {
        "sample_key": sample.sample_key,
        "branch_id": sample.branch_id,
        "role": str(role),
        "classification": sample.classification.value,
        "ordinal": (
            sample.continuation_epoch if ordinal is None else int(ordinal)
        ),
        "coordinates": coordinates,
        "metadata": publication_metadata,
    }
