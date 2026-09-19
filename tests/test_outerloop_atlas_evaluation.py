from __future__ import annotations

from collections import deque
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from MissionOptions import MissionOptions
from OuterLoop.atlas import (
    FamilySample,
    ParameterTransform,
    ParameterVector,
    SampleClassification,
)
from OuterLoop.atlas_evaluation import (
    AtlasEvaluationError,
    ContinuationSeed,
    FamilySampleEvaluator,
    HardwareVariantMaterializer,
    PreparedAtlasCase,
    RescuePolicy,
    TrialXSeed,
    transport_trialx,
)
from OuterLoop.canonical import file_sha256
from OuterLoop.evaluator import EMTGCaseBuilder, EMTGResultParser
from OuterLoop.hardware import PowerSystemLibrary, PropulsionSystemLibrary, SpacecraftInspection
from OuterLoop.model import (
    CandidateRecord,
    ComparisonContext,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    Genotype,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
)
from OuterLoop.parameters import (
    BOL_POWER,
    CONSTANT_THRUST,
    ELECTRIC_PROPELLANT_CAPACITY,
    ENGINE_DUTY_CYCLE,
    ENGINE_COUNT,
    LAUNCH_C3_INPUT,
    LAUNCH_EPOCH,
    MAXIMUM_MASS,
    THRUST_SCALE,
    AtlasAnchor,
    ParameterApplicationPlan,
    ParameterRegistry,
)
from OuterLoop.storage import ArtifactStore
from OuterLoop.workers import QueueRequest


ROOT = Path(__file__).resolve().parents[1]
HARDWARE = ROOT / "testatron" / "HardwareModels"
UNIVERSE = ROOT / "testatron" / "universe"
SOURCE = ROOT / "testatron" / "tests" / "transcription_tests" / "FBLT_EMintercept.emtgopt"
METRICS = {"mission_events": ({"julian_date_mjd": 60000.25, "c3": 5.0},)}


def _options_file(tmp_path: Path, mode: int) -> tuple[MissionOptions, Path]:
    options = MissionOptions(str(SOURCE))
    options.HardwarePath = str(HARDWARE)
    options.universe_folder = str(UNIVERSE)
    options.SpacecraftModelInput = mode
    if mode == 1:
        options.SpacecraftOptionsFile = "default.emtg_spacecraftopt"
    output = tmp_path / f"mode{mode}.emtgopt"
    options.write_options_file(str(output), True)
    return options, output


def _phenotype(prepared: PreparedAtlasCase, phase_type: int) -> MissionPhenotype:
    return MissionPhenotype(
        {"__atlas_case_v1__": prepared.to_dict()},
        (
            JourneyPhenotype(
                "Earth", "Mars", (), {"phase_type": phase_type},
                (PhasePhenotype("Mars", {"phase_type": phase_type, "dsm_count": 0}),),
            ),
        ),
    )


def _build(tmp_path: Path, options: MissionOptions, source: Path, requested: dict[str, object],
           *, architecture_change_allowed: bool = False):
    anchor = AtlasAnchor.from_options(options, HARDWARE, metrics=METRICS)
    plan = ParameterRegistry().plan_mutations(anchor, requested)
    store = ArtifactStore(tmp_path / "artifacts")
    prepared = HardwareVariantMaterializer(HARDWARE, store).prepare(
        options, plan, architecture_id=anchor.architecture.architecture_id,
        architecture_change_allowed=architecture_change_allowed,
    )
    builder = EMTGCaseBuilder(source, universe_folder=UNIVERSE, hardware_path=HARDWARE)
    output = builder.build(
        _phenotype(prepared, int(options.Journeys[0].phase_type)),
        tmp_path / "case", "atlas_case", evaluation_seed=7,
        budget={"inner_loop": "nlp"}, initial_guess=None,
        atlas_case=prepared.to_dict(), artifact_store_root=store.root,
    )
    return MissionOptions(str(output)), prepared


def test_result_parser_retains_trialx_bounds_in_external_units():
    result = EMTGResultParser().parse(
        ROOT / "testatron" / "tests" / "global_mission_options"
        / "globalmissionoptions_MGALT_constrDryMass.emtg"
    )
    assert result.complete
    assert len(result.xlowerbounds) == len(result.xdescriptions)
    assert len(result.xupperbounds) == len(result.xdescriptions)
    assert result.xlowerbounds[0] < result.decision_vector[0] < result.xupperbounds[0]


def test_real_mode0_case_builder_materializes_power_and_propulsion_variants(tmp_path):
    options, source = _options_file(tmp_path, 0)
    power_source = HARDWARE / options.PowerSystemsLibraryFile
    propulsion_source = HARDWARE / options.PropulsionSystemsLibraryFile
    before = (file_sha256(power_source), file_sha256(propulsion_source))
    built, prepared = _build(tmp_path, options, source, {BOL_POWER: 6, THRUST_SCALE: "0.75"})

    overlay = Path(built.HardwarePath)
    power = PowerSystemLibrary.from_file(overlay / built.PowerSystemsLibraryFile)
    propulsion = PropulsionSystemLibrary.from_file(overlay / built.PropulsionSystemsLibraryFile)
    assert power.get(built.PowerSystemKey).p0_kw == 6
    assert propulsion.get(built.ElectricPropulsionSystemKey).thrust_scale == Decimal("0.75")
    assert len(prepared.variants) == 2
    assert before == (file_sha256(power_source), file_sha256(propulsion_source))


def test_real_constant_thrust_variant_and_architecture_engine_count_gate(tmp_path):
    options, source = _options_file(tmp_path, 0)
    options.ElectricPropulsionSystemKey = "WarpDrive"
    options.write_options_file(str(source), True)
    built, prepared = _build(tmp_path, options, source, {CONSTANT_THRUST: "12.5"})
    propulsion = PropulsionSystemLibrary.from_file(
        Path(built.HardwarePath) / built.PropulsionSystemsLibraryFile
    )
    assert propulsion.get("WarpDrive").constant_thrust_n == Decimal("12.5")
    assert prepared.variants[0].variant_id != prepared.variants[0].content_sha256

    anchor = AtlasAnchor.from_options(options, HARDWARE, metrics=METRICS)
    changed = ParameterRegistry().plan_mutations(anchor, {ENGINE_COUNT: 2})
    materializer = HardwareVariantMaterializer(HARDWARE, ArtifactStore(tmp_path / "engine-artifacts"))
    with pytest.raises(AtlasEvaluationError, match="architecture stratum"):
        materializer.prepare(options, changed, architecture_id=anchor.architecture.architecture_id)
    allowed = materializer.prepare(
        options, changed, architecture_id="new-architecture",
        architecture_change_allowed=True,
    )
    assert allowed.architecture_change_allowed


def test_real_mode1_case_builder_aggregates_spacecraft_transformations(tmp_path):
    options, source = _options_file(tmp_path, 1)
    baseline = HARDWARE / options.SpacecraftOptionsFile
    before = file_sha256(baseline)
    built, prepared = _build(tmp_path, options, source, {
        ELECTRIC_PROPELLANT_CAPACITY: 800,
        BOL_POWER: 6,
        THRUST_SCALE: "0.8",
    })

    spacecraft = SpacecraftInspection.from_file(Path(built.HardwarePath) / built.SpacecraftOptionsFile)
    assert spacecraft.global_electric_constraint_enabled
    assert spacecraft.global_electric_capacity_kg == 800
    assert spacecraft.effective_power_systems()[0].p0_kw == 6
    assert spacecraft.effective_electric_propulsion_systems()[0].thrust_scale == Decimal("0.8")
    assert len(prepared.variants) == 1
    assert file_sha256(baseline) == before


def test_real_mode2_case_builder_applies_global_and_launch_controls(tmp_path):
    options, source = _options_file(tmp_path, 2)
    options.Journeys[0].bounded_departure_date = 1
    options.write_options_file(str(source), True)
    built, prepared = _build(tmp_path, options, source, {
        LAUNCH_EPOCH: "60001.5",
        LAUNCH_C3_INPUT: 5,
        MAXIMUM_MASS: 1200,
        ELECTRIC_PROPELLANT_CAPACITY: 700,
        ENGINE_DUTY_CYCLE: "0.8",
        BOL_POWER: 6,
        THRUST_SCALE: "0.7",
    })
    assert built.launch_window_open_date == pytest.approx(60001.5)
    assert built.Journeys[0].wait_time_bounds == [0.0, 0.0]
    assert built.Journeys[0].departure_date_bounds == pytest.approx([60001.5, 60001.5])
    assert built.Journeys[0].initial_impulse_bounds == pytest.approx([5**0.5, 5**0.5])
    assert built.maximum_mass == pytest.approx(1200)
    assert built.maximum_electric_propellant == pytest.approx(700)
    assert built.enable_electric_propellant_tank_constraint == 1
    assert built.engine_duty_cycle == pytest.approx(0.8)
    assert built.power_at_1_AU == pytest.approx(6)
    assert built.thrust_scale_factor == pytest.approx(0.7)
    assert prepared.launch_performance["c3_km2_s2"] == "5"


def test_trialx_nearest_secant_adjustment_and_bounds():
    descriptions = (
        "j0p0FBLTEphemerisPeggedLaunchDirectInsertion: event left state epoch",
        "j0p0FBLTEphemerisPeggedLaunchDirectInsertion: magnitude of outgoing velocity asymptote",
        "j0p0FBLT: phase flight time",
    )
    parent = TrialXSeed("eval-parent", descriptions, (60000, 2, 100), (59000, 0, 0), (61000, 10, 500))
    grandparent = TrialXSeed("eval-grand", descriptions, (59999, 1.5, 90), (59000, 0, 0), (61000, 10, 500))
    transported, manifest = transport_trialx(
        parent, {LAUNCH_EPOCH: 60002, LAUNCH_C3_INPUT: 9},
        grandparent=grandparent, axis_values=(3, 2, 1),
        axis_transform=ParameterTransform.LINEAR,
    )
    assert manifest.method == "secant"
    assert manifest.secant_ratio == pytest.approx(1)
    assert transported.decision_vector == pytest.approx((60002, 3, 110))

    with pytest.raises(AtlasEvaluationError, match="above source bounds"):
        transport_trialx(
            TrialXSeed("p", ("j0p0: other",), (1,), (0,), (1.5,)), {},
            grandparent=TrialXSeed("g", ("j0p0: other",), (0,), (0,), (1.5,)),
            axis_values=(2, 1, 0),
        )


def _sample(value: int, *, classification=SampleClassification.UNKNOWN) -> FamilySample:
    return FamilySample(
        "family-test", "architecture-test", ComparisonContext("context", 0, "full"),
        ParameterVector.from_mapping({MAXIMUM_MASS: value}), "full",
        classification=classification,
    )


class _ScriptedEvaluator:
    def __init__(self, statuses):
        self.statuses = deque(statuses)
        self.requests = []

    def context_identity(self):
        return {"type": "scripted-atlas"}

    def evaluate(self, request, cancel_event=None):
        self.requests.append(request)
        status = self.statuses.popleft()
        guess = request.initial_guess
        return EvaluationResult(
            request.evaluation_key, request.candidate.candidate_id, status, request.fidelity,
            solver_violation=None if status is EvaluationStatus.FEASIBLE else 0.1,
            metrics={
                "emtg_objective": 1.0,
                "xdescriptions": tuple(guess["xdescriptions"]),
                "decision_vector": tuple(guess["decision_vector"]),
                "decision_vector_lower_bounds": (0.0,),
                "decision_vector_upper_bounds": (2000.0,),
            },
        )


def _rescue_inputs():
    source_sample = _sample(1000, classification=SampleClassification.FEASIBLE_FOUND)
    source_result = EvaluationResult(
        "eval-source", "candidate-source", EvaluationStatus.FEASIBLE, "full",
        metrics={
            "xdescriptions": ("j0p0FBLT: phase flight time",),
            "decision_vector": (100.0,),
            "decision_vector_lower_bounds": (0.0,),
            "decision_vector_upper_bounds": (2000.0,),
        },
    )
    seed = ContinuationSeed.from_result(source_sample, source_result)
    candidate = CandidateRecord("candidate", Genotype(), MissionPhenotype({}, ()), 0)
    prepared = PreparedAtlasCase(
        "architecture-test", 2,
        ParameterApplicationPlan(ParameterVector(()), ()), (),
    )
    policy = RescuePolicy.balanced({
        "inner_loop": "nlp", "nlp_max_run_time": 10,
        "nlp_major_iterations": 100, "mbh_max_trials": 50,
    })
    return seed, candidate, prepared, policy


def test_rescue_stops_on_first_feasible_and_confirms_only_full_negative_cascade():
    seed, candidate, prepared, policy = _rescue_inputs()
    feasible_evaluator = _ScriptedEvaluator([EvaluationStatus.FEASIBLE])
    feasible = FamilySampleEvaluator(feasible_evaluator, policy).evaluate(
        _sample(1100), candidate, prepared, [seed]
    )
    assert feasible.sample.classification is SampleClassification.FEASIBLE_FOUND
    assert len(feasible.attempts) == 1
    family_context = feasible_evaluator.requests[0].context["family"]
    assert family_context["family_id"] == feasible.sample.family_id
    assert family_context["sample_key"] == feasible.sample.sample_key
    assert family_context["coordinates"] == {
        "spacecraft.maximum_mass_kg": "1100"
    }

    negative_evaluator = _ScriptedEvaluator([EvaluationStatus.EMTG_INFEASIBLE] * 4)
    negative = FamilySampleEvaluator(negative_evaluator, policy).evaluate(
        _sample(1100), candidate, prepared, [seed]
    )
    assert negative.sample.classification is SampleClassification.CONFIRMED_NOT_FOUND
    assert len(negative.attempts) == 4
    assert sum(request.budget["inner_loop"] == "mbh" for request in negative_evaluator.requests) == 2


def test_timeout_or_incomplete_transport_keeps_exhausted_rescue_unknown():
    seed, candidate, prepared, policy = _rescue_inputs()
    evaluator = _ScriptedEvaluator([
        EvaluationStatus.TIMED_OUT,
        EvaluationStatus.EMTG_INFEASIBLE,
        EvaluationStatus.EMTG_INFEASIBLE,
        EvaluationStatus.EMTG_INFEASIBLE,
    ])
    outcome = FamilySampleEvaluator(evaluator, policy).evaluate(
        _sample(1100), candidate, prepared, [seed]
    )
    assert outcome.sample.classification is SampleClassification.UNKNOWN
    assert outcome.sample.confidence is None


def test_global_discovery_then_confirmation_executes_only_missing_stable_rescue_stages():
    seed, candidate, prepared, policy = _rescue_inputs()
    discovery_evaluator = _ScriptedEvaluator([
        EvaluationStatus.EMTG_INFEASIBLE,
        EvaluationStatus.EMTG_INFEASIBLE,
    ])
    discovery = FamilySampleEvaluator(discovery_evaluator, policy).evaluate(
        _sample(1100), candidate, prepared, [seed],
        evaluation_profile="global_discovery",
    )
    assert discovery.sample.classification is SampleClassification.UNKNOWN
    discovery_ordinals = {
        item.rescue_stage_ordinal for item in discovery.attempts
    }
    assert discovery_ordinals == {0, 4}

    confirmation_evaluator = _ScriptedEvaluator([
        EvaluationStatus.EMTG_INFEASIBLE,
        EvaluationStatus.EMTG_INFEASIBLE,
    ])
    confirmation = FamilySampleEvaluator(confirmation_evaluator, policy).evaluate(
        discovery.sample, candidate, prepared, [seed],
        evaluation_profile="confirmation",
        previous_attempts=discovery.attempts,
        previous_results=discovery.results,
    )
    assert confirmation.sample.classification is SampleClassification.CONFIRMED_NOT_FOUND
    confirmation_ordinals = {
        item.rescue_stage_ordinal for item in confirmation.attempts
    }
    assert confirmation_ordinals == {3, 5}
    assert discovery_ordinals.isdisjoint(confirmation_ordinals)


def test_atlas_request_identity_and_queue_round_trip_are_path_independent():
    seed, candidate, prepared, _ = _rescue_inputs()
    physical = replace(candidate, phenotype=replace(
        candidate.phenotype,
        mission={"__atlas_case_v1__": prepared.to_dict()},
    ))
    request = EvaluationRequest(
        physical, "full", 9, {"inner_loop": "nlp"}, seed.trialx.initial_guess(),
        {"atlas_case_v1": prepared.to_dict(), "inner_seed_set": (9,)},
    )
    encoded = QueueRequest.from_evaluation_request(request).to_dict()
    decoded = QueueRequest.from_dict(encoded)
    assert decoded.evaluation_key == request.evaluation_key
    assert decoded.request.context["atlas_case_v1"] == prepared.to_dict()
