from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from OuterLoop.atlas import (
    ArchitectureSignature,
    AtlasContractError,
    AtlasDefinition,
    AxisDefinition,
    BranchDefinition,
    ContinuationEdge,
    FamilyAttempt,
    FamilyDefinition,
    FamilySample,
    FeasibilityPolicyRef,
    HardwareArtifactIdentity,
    HardwareBinding,
    HardwareTransformation,
    HardwareVariant,
    ParameterTransform,
    ParameterVector,
    SampleClassification,
)
from OuterLoop.model import (
    CandidateRecord,
    ComparisonContext,
    EvaluationRequest,
    EvaluationStatus,
    Genotype,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
)
from OuterLoop.seeds import SeedArtifact


SHA_A = "a" * 64
SHA_B = "b" * 64
ATLAS_ID = "atlas-11111111111111111111111111111111"
CONTEXT = ComparisonContext("comparison-1", 2, "full")


def architecture(**changes: object) -> ArchitectureSignature:
    values: dict[str, object] = {
        "topology": {
            "journeys": [{
                "endpoints": ["Earth", "Mars"],
                "flybys": ["Venus"],
                "phase_types": [3, 3],
                "dsm_counts": [0, 1],
                "boundary_types": [0, 0, 0, 1],
                "constraints": ["dry_mass", "arrival_declination"],
            }],
            "resonance": {"selected": "none"},
        },
        "spacecraft_model_input": 2,
        "hardware_bindings": (
            HardwareBinding(
                "launch_vehicle", SHA_A, ("Atlas_V_401",),
                {"throttle_tables": [{"sha256": SHA_B, "key": "table"}]},
            ),
        ),
        "effective_thruster_modes": (3,),
        "engine_count": 2,
    }
    values.update(changes)
    return ArchitectureSignature(**values)  # type: ignore[arg-type]


def family(**changes: object) -> FamilyDefinition:
    values: dict[str, object] = {
        "atlas_id": ATLAS_ID,
        "architecture_id": architecture().architecture_id,
        "anchor_candidate_id": "candidate-anchor",
        "anchor_evaluation_key": "evaluation-anchor",
        "comparison_context": CONTEXT,
        "anchor_parameters": ParameterVector.from_mapping({
            "launch.epoch_mjd": "60000.0",
            "spacecraft.maximum_mass_kg": "2000.00",
        }),
        "axes": (
            AxisDefinition("spacecraft.maximum_mass_kg", "1000", "3000", "100"),
            AxisDefinition("launch.epoch_mjd", "59000", "61000", "10"),
        ),
        "feasibility_policy": FeasibilityPolicyRef(
            "default", 1, {"confirmation_attempts": 3}
        ),
        "planner_configuration": {"bisection": True, "max_depth": 8},
    }
    values.update(changes)
    return FamilyDefinition(**values)  # type: ignore[arg-type]


def sample(**changes: object) -> FamilySample:
    definition = family()
    values: dict[str, object] = {
        "family_id": definition.family_id,
        "architecture_id": definition.architecture_id,
        "comparison_context": CONTEXT,
        "parameters": ParameterVector.from_mapping({
            "spacecraft.maximum_mass_kg": "2100.000",
            "launch.epoch_mjd": "60010",
        }),
        "fidelity": "full",
    }
    values.update(changes)
    return FamilySample(**values)  # type: ignore[arg-type]


def schema3_phenotype() -> MissionPhenotype:
    return MissionPhenotype(
        {
            "launch_window_open_date": 60000,
            "launch_vehicle": "Atlas_V_401",
            "number_of_electric_propulsion_systems": 2,
        },
        (
            JourneyPhenotype(
                "Earth", "Mars", ("Venus",),
                {
                    "phase_type": 3,
                    "departure_class": 0,
                    "departure_type": 0,
                    "arrival_class": 0,
                    "arrival_type": 1,
                },
                (PhasePhenotype("Mars", {"phase_type": 3, "dsm_count": 1}),),
            ),
        ),
    )


def test_golden_schema_v1_serialization_and_hashes():
    atlas = AtlasDefinition(
        ATLAS_ID, "Mars atlas", "description", ("b", "comparison-1", "a", "a")
    )
    assert atlas.to_dict() == {
        "schema_version": 1,
        "atlas_id": ATLAS_ID,
        "name": "Mars atlas",
        "description": "description",
        "comparison_context_ids": ["a", "b", "comparison-1"],
        "family_ids_by_architecture": {},
    }
    assert AtlasDefinition.from_dict(atlas.to_dict()) == atlas

    signature = architecture()
    definition = family()
    point = sample()
    branch = BranchDefinition(definition.family_id, point.sample_key, None, 4, ("z", "a"))
    edge = ContinuationEdge(
        definition.family_id, point.sample_key, "child", "continuation",
        Decimal("0.2500"), {"classification_changed": True},
    )
    variant = HardwareVariant(
        "propulsion_library", HardwareArtifactIdentity("propulsion_library", SHA_A),
        (
            HardwareTransformation("power.beginning_of_life_kw", "row:p0", Decimal("6.00")),
            HardwareTransformation("propulsion.thrust_scale", "row:scale", Decimal("0.900")),
        ),
    )

    # These values are deliberately fixed compatibility witnesses for every
    # new content-hash namespace.
    assert signature.architecture_id == "68ba38daf9d9077edb32c62074a2dc02d14d280bea1e2b48cae3375d70c6b2a8"
    assert definition.family_id == "fam_430e8fec9b1d4dd20233785cc8438e23ceadce4ccb90ac7ef8b397cf7ba0388b"
    assert point.sample_key == "3726f28ce17ce116f8b686bfea6d86ae6844d5536c665e67ef6f582ce06ed501"
    assert branch.branch_id == "cc9b1169596b565235a0ceeda849ccb69f3fdfec4a929ce6c761949a669fc5f6"
    assert edge.edge_id == "bff2e51892b27343151a551ee10fee97b26915690018fbc979908a4101f60e48"
    assert variant.variant_id == "16141670a3537944e29527749cff51d32596cb568300f3e04f0b1ee89770dcc0"

    assert ArchitectureSignature.from_dict(signature.to_dict()) == signature
    assert FamilyDefinition.from_dict(definition.to_dict()) == definition
    assert FamilySample.from_dict(point.to_dict()) == point
    assert BranchDefinition.from_dict(branch.to_dict()) == branch
    assert ContinuationEdge.from_dict(edge.to_dict()) == edge
    assert HardwareVariant.from_dict(variant.to_dict()) == variant
    assert variant.to_dict()["transformations"] == [
        {
            "key": "power.beginning_of_life_kw", "numeric_type": "decimal",
            "value": "6", "target": "row:p0",
        },
        {
            "key": "propulsion.thrust_scale", "numeric_type": "decimal",
            "value": "0.9", "target": "row:scale",
        },
    ]

    attempt = FamilyAttempt(
        point.sample_key, "evaluation-1", "candidate-1", 2, "warm-start", "full",
        {"iterations": 1000}, EvaluationStatus.FEASIBLE, 0.0, 12.5,
        {"result": "sha256:artifact"},
    )
    assert attempt.attempt_id == "evaluation-1"
    assert attempt.association_key == (point.sample_key, "evaluation-1")
    assert FamilyAttempt.from_dict(attempt.to_dict()) == attempt

    updated_atlas = atlas.with_family(definition)
    assert updated_atlas.atlas_id == atlas.atlas_id
    assert updated_atlas.families_in_stratum(definition.architecture_id) == (
        definition.family_id,
    )
    assert AtlasDefinition.from_dict(updated_atlas.to_dict()) == updated_atlas
    with pytest.raises(AtlasContractError, match="different atlas"):
        atlas.with_family(replace(definition, atlas_id="atlas-22222222222222222222222222222222"))
    with pytest.raises(AtlasContractError, match="not permitted"):
        AtlasDefinition(ATLAS_ID, "restricted", "", ("other",)).with_family(definition)


def test_identity_canonicalization_and_exact_decimal_contracts():
    vector_a = ParameterVector.from_mapping({"b": "2.00", "a": "1.0"})
    vector_b = ParameterVector.from_mapping({"a": Decimal("1.000"), "b": 2})
    assert vector_a.to_dict() == vector_b.to_dict()
    assert [item["key"] for item in vector_a.to_dict()["values"]] == ["a", "b"]

    reordered = replace(
        family(),
        anchor_parameters=ParameterVector(tuple(reversed(family().anchor_parameters.values))),
        axes=tuple(reversed(family().axes)),
        planner_configuration={"max_depth": 8, "bisection": True},
    )
    assert reordered.family_id == family().family_id

    with pytest.raises(AtlasContractError):
        ParameterVector.from_mapping({"x": float("nan")})
    with pytest.raises(AtlasContractError):
        ParameterVector.from_mapping({"x": True})
    with pytest.raises(AtlasContractError):
        ParameterVector.from_mapping({"count": "1.5"}, {"count"})
    with pytest.raises(AtlasContractError):
        AxisDefinition("x", 0, 1, 0)
    with pytest.raises(AtlasContractError, match="boolean"):
        HardwareTransformation("x", "target", True)
    with pytest.raises(AtlasContractError, match="duplicate hardware"):
        ArchitectureSignature(
            {}, 2,
            (HardwareBinding("same", SHA_A), HardwareBinding("same", SHA_B)),
            (3,), 1,
        )


def test_architecture_family_and_sample_identity_boundaries():
    base_architecture = architecture()
    assert replace(base_architecture, engine_count=3).architecture_id != base_architecture.architecture_id
    assert replace(base_architecture, topology={"journeys": []}).architecture_id != base_architecture.architecture_id
    assert replace(
        base_architecture,
        hardware_bindings=(HardwareBinding("launch_vehicle", SHA_B, ("Atlas_V_401",)),),
    ).architecture_id != base_architecture.architecture_id

    base_family = family()
    assert replace(
        base_family,
        anchor_parameters=base_family.anchor_parameters.with_values(
            {"spacecraft.maximum_mass_kg": Decimal("2100")}
        ),
    ).family_id != base_family.family_id
    assert replace(
        base_family,
        comparison_context=ComparisonContext("comparison-2", 2, "full"),
    ).family_id != base_family.family_id

    point = sample()
    bookkeeping = replace(
        point,
        parent_sample_key="parent", continuation_epoch=9, branch_id="branch",
        classification=SampleClassification.FEASIBLE_FOUND, confidence=0.99,
        best_evaluation_key="best", metrics={"mass": 42},
    )
    assert bookkeeping.sample_key == point.sample_key
    assert replace(
        point,
        parameters=point.parameters.with_values({"launch.epoch_mjd": Decimal("60020")}),
    ).sample_key != point.sample_key
    assert replace(point, fidelity="coarse").sample_key != point.sample_key
    assert replace(
        point, comparison_context=ComparisonContext("comparison-2", 2, "full")
    ).sample_key != point.sample_key
    assert replace(point, family_id="another-family").sample_key != point.sample_key


def test_hardware_variant_identity_is_path_free_order_independent_and_conflict_safe():
    baseline_at_path_a = HardwareArtifactIdentity("power_library", SHA_A)
    baseline_at_path_b = HardwareArtifactIdentity("power_library", SHA_A)
    transformations = (
        HardwareTransformation("propulsion.thrust_scale", "propulsion:scale", "0.8"),
        HardwareTransformation("power.beginning_of_life_kw", "power:p0", "5.0"),
    )
    first = HardwareVariant("power_library", baseline_at_path_a, transformations)
    relocated = HardwareVariant("power_library", baseline_at_path_b, tuple(reversed(transformations)))
    assert first.variant_id == relocated.variant_id
    assert replace(first, baseline=HardwareArtifactIdentity("power_library", SHA_B)).variant_id != first.variant_id
    assert HardwareVariant(
        "power_library", baseline_at_path_a,
        (HardwareTransformation("power.beginning_of_life_kw", "power:p0", "6"),),
    ).variant_id != first.variant_id
    with pytest.raises(AtlasContractError, match="conflicting"):
        HardwareVariant(
            "power_library", baseline_at_path_a,
            (
                HardwareTransformation("a", "same", 1),
                HardwareTransformation("b", "same", 2),
            ),
        )


def test_schema3_identities_remain_distinct_and_golden():
    mission = schema3_phenotype()
    candidate = CandidateRecord("individual", Genotype(), mission, 0)
    request = EvaluationRequest(
        candidate, "full", 17, {"iterations": 100}, {"source": "cold"},
        {"inner_seed_set": (17, 19), "binary": "emtg"},
    )
    seed = SeedArtifact.create("known.emtg", mission, ("x",), (1.25,), True)
    point = sample()

    assert candidate.candidate_id == "b9161a4bb3a3470a185bac8c9188def0ccfe424ff0ab14e7079831643faed735"
    assert request.evaluation_key == "3aebb04f48aa63ab4dbf834c93da95d33ccb02e591d35da9b1cf5f897058bb6d"
    assert seed.seed_id == "38a2da24841db1b57168cb51e4c5e4814fbada684992fe2b3bb9aeb44ae23a42"
    assert len({candidate.candidate_id, architecture().architecture_id,
                family().family_id, point.sample_key, request.evaluation_key}) == 5

    changed_budget = replace(request, budget={"iterations": 200})
    changed_guess = replace(request, initial_guess={"source": "warm"})
    assert changed_budget.evaluation_key != request.evaluation_key
    assert changed_guess.evaluation_key != request.evaluation_key
    assert point.sample_key == sample().sample_key

    other_family_point = replace(point, family_id="another-family")
    first_association = FamilyAttempt(
        point.sample_key, request.evaluation_key, candidate.candidate_id, 0, None,
        "full", request.budget, EvaluationStatus.FEASIBLE,
    )
    second_association = replace(first_association, sample_key=other_family_point.sample_key)
    assert first_association.evaluation_key == second_association.evaluation_key
    assert first_association.association_key != second_association.association_key

    # The compatibility alias remains excluded from schema-3 identity changes.
    alias = replace(
        mission,
        mission={"launch_epoch": 60000, **{
            key: value for key, value in mission.mission.items()
            if key != "launch_window_open_date"
        }},
    )
    assert alias.identity == mission.identity
