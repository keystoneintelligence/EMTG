from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyEMTG.OuterLoop.atlas import (
    AxisDefinition,
    FamilyDefinition,
    FamilySample,
    FeasibilityPolicyRef,
    ParameterVector,
    SampleClassification,
)
from PyEMTG.OuterLoop.model import ComparisonContext
from PyEMTG.Solution import family_membership_payload, family_resource_payload


def _family() -> FamilyDefinition:
    context = ComparisonContext("comparison-1", 1, "full")
    return FamilyDefinition(
        "atlas-11111111111111111111111111111111",
        "architecture-1",
        "candidate-anchor",
        "evaluation-anchor",
        context,
        ParameterVector.from_mapping({"launch_mjd": "60000", "mass_kg": "2000"}),
        (
            AxisDefinition("launch_mjd", "59000", "61000", "10"),
            AxisDefinition("mass_kg", "1000", "3000", "100"),
        ),
        FeasibilityPolicyRef("default"),
    )


def test_operational_family_serializes_as_deepspace_family_payload():
    family = _family()
    payload = family_resource_payload(
        family,
        name="Earth-Mars launch family",
        tags=("mars", "atlas", "mars"),
    )

    assert re.fullmatch(r"fam_[0-9a-f]{64}", family.family_id)
    assert payload["family_id"] == family.family_id
    assert payload["definition"] == family.to_dict()
    assert [axis["key"] for axis in payload["axes"]] == [
        "launch_mjd",
        "mass_kg",
    ]
    assert payload["tags"] == ["atlas", "mars"]
    assert json.loads(json.dumps(payload)) == payload
    assert FamilyDefinition.from_dict(payload["definition"]) == family


def test_operational_sample_serializes_as_deepspace_membership_payload():
    family = _family()
    sample = FamilySample(
        family.family_id,
        family.architecture_id,
        family.comparison_context,
        ParameterVector.from_mapping(
            {"launch_mjd": Decimal("60010.0"), "mass_kg": Decimal("2100.00")}
        ),
        "full",
        continuation_epoch=3,
        branch_id="nominal",
        classification=SampleClassification.FEASIBLE_FOUND,
        confidence=0.95,
        best_evaluation_key="evaluation-best",
    )

    payload = family_membership_payload(sample, metadata={"campaign_id": "run-1"})

    assert payload["sample_key"] == sample.sample_key
    assert payload["branch_id"] == "nominal"
    assert payload["classification"] == "feasible_found"
    assert payload["ordinal"] == 3
    assert payload["coordinates"] == {"launch_mjd": "60010", "mass_kg": "2100"}
    assert payload["metadata"]["campaign_id"] == "run-1"
    assert payload["metadata"]["emtg_family_sample"] == sample.to_dict()
    assert json.loads(json.dumps(payload)) == payload
