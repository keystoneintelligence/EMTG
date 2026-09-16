from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from OuterLoop.atlas_evaluation import (
    FamilySampleEvaluator,
    HardwareVariantMaterializer,
    RescuePolicy,
    TrialXSeed,
    apply_prepared_atlas_case,
    atlas_candidate,
    transport_trialx,
    verify_written_atlas_case,
)
from OuterLoop.atlas import (
    AtlasDefinition,
    AxisDefinition,
    FamilyDefinition,
    FeasibilityPolicyRef,
    ParameterVector,
    SampleClassification,
)
from OuterLoop.evaluator import EMTGEvaluator, EMTGResultParser
from OuterLoop.family import AtlasFamilyTaskExecutor, FamilyContinuationWorker
from OuterLoop.genome import GenomeSchema, random_genotype
from OuterLoop.model import (
    CandidateRecord,
    ComparisonContext,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
)
from OuterLoop.randomness import random_stream
from OuterLoop.config import SearchConfig
from OuterLoop.parameters import (
    AtlasAnchor, BOL_POWER, CONSTANT_THRUST, ELECTRIC_PROPELLANT_CAPACITY,
    ENGINE_DUTY_CYCLE, LAUNCH_C3_INPUT, LAUNCH_EPOCH, MAXIMUM_MASS,
    THRUST_SCALE, ParameterRegistry,
)
from OuterLoop.storage import ArtifactStore, EvaluationCache
from OuterLoop.workers import LocalWorkerBackend
from MissionOptions import MissionOptions
from run_asteroid_integration import read_mission_name, validate_output_dir


ROOT = Path(__file__).resolve().parents[1]


def _nearby_fblt_targets(anchor: AtlasAnchor) -> dict[str, Decimal]:
    discovered = ParameterRegistry().discover_mapping(anchor)
    targets = _nearby_aeps_targets(anchor)
    epoch = discovered[LAUNCH_EPOCH]
    assert epoch.available and epoch.current_value is not None
    targets[LAUNCH_EPOCH] = Decimal(str(epoch.current_value)) + Decimal("0.01")
    c3 = discovered[LAUNCH_C3_INPUT]
    assert c3.available
    reference = c3.current_value if c3.current_value is not None else c3.suggested_value
    assert reference is not None
    candidate = Decimal(str(reference)) + Decimal("0.01")
    bounds = c3.definition.hard_bounds.intersect(c3.effective_bounds)
    if not bounds.contains(candidate):
        candidate = Decimal(str(reference)) - Decimal("0.01")
    assert bounds.contains(candidate)
    targets[LAUNCH_C3_INPUT] = candidate
    return targets


def _nearby_impulsive_targets(anchor: AtlasAnchor) -> dict[str, Decimal]:
    """Small, supported perturbations for the reproducible MGAnDSMs seed."""
    discovered = ParameterRegistry().discover_mapping(anchor)
    targets: dict[str, Decimal] = {}
    for key in (MAXIMUM_MASS, BOL_POWER):
        resolved = discovered[key]
        assert resolved.available and resolved.current_value is not None
        current = Decimal(str(resolved.current_value))
        candidate = current * Decimal("1.0001")
        bounds = resolved.definition.hard_bounds.intersect(
            resolved.effective_bounds
        )
        assert bounds.contains(candidate)
        targets[key] = candidate

    epoch = discovered[LAUNCH_EPOCH]
    assert epoch.available and epoch.current_value is not None
    targets[LAUNCH_EPOCH] = (
        Decimal(str(epoch.current_value)) + Decimal("0.001")
    )

    c3 = discovered[LAUNCH_C3_INPUT]
    assert c3.available
    reference = c3.current_value if c3.current_value is not None else c3.suggested_value
    assert reference is not None
    candidate = Decimal(str(reference)) + Decimal("0.001")
    bounds = c3.definition.hard_bounds.intersect(c3.effective_bounds)
    assert bounds.contains(candidate)
    targets[LAUNCH_C3_INPUT] = candidate
    return targets


@pytest.mark.emtg_integration
@pytest.mark.skipif(
    os.environ.get("EMTG_RUN_OUTERLOOP_INTEGRATION") != "1",
    reason="set EMTG_RUN_OUTERLOOP_INTEGRATION=1 for the bounded real solver test",
)
def test_bounded_real_emtg_evaluation_is_typed_and_isolated(tmp_path):
    evaluator = EMTGEvaluator(
        base_case=ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt",
        executable=ROOT / "bin" / "EMTGv9.exe",
        run_directory=tmp_path,
        timeout_seconds=15,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
        brief_executable=ROOT / "depend" / "cspice" / "exe" / "brief.exe",
        ephemeris_source_override=1,
    )
    genotype = random_genotype(
        GenomeSchema(SearchConfig(max_journeys=1, fixed_start="Earth", fixed_final="Mars")),
        random_stream(1, "integration"),
    )
    phenotype = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Earth",
                "Mars",
                (),
                {"phase_type": 6, "arrival_type": 2},
                (PhasePhenotype("Mars", {"phase_type": 6, "dsm_count": 1}),),
            ),
        ),
    )
    candidate = CandidateRecord("real-integration", genotype, phenotype, 0)
    request = EvaluationRequest(
        candidate,
        "short",
        7,
        {
            "inner_loop": "mbh",
            "mbh_max_run_time": 5,
            "mbh_max_trials": 100,
            "nlp_solver_type": 2,
            "feasibility_tolerance": 1.0e-8,
            "nlp_max_run_time": 5,
            "nlp_major_iterations": 100,
        },
        context={"evaluator": evaluator.context_identity()},
    )
    result = evaluator.evaluate(request)
    assert result.status is EvaluationStatus.FEASIBLE
    assert result.metrics["emtg_objective"] < 0.0
    assert result.metrics["flight_time"] > 0.0
    assert Path(result.artifacts["case_directory"]).is_dir()
    assert Path(result.artifacts["options"]).is_file()
    assert Path(result.artifacts["stdout"]).is_file()
    assert result.provenance["process_arguments"][0] == str((ROOT / "bin" / "EMTGv9.exe").resolve())


@pytest.mark.emtg_integration
@pytest.mark.skipif(
    os.environ.get("EMTG_RUN_OUTERLOOP_INTEGRATION") != "1",
    reason="set EMTG_RUN_OUTERLOOP_INTEGRATION=1 for the atlas warm-start smoke test",
)
@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize("case_kind", ["baseline", "nearby"])
def test_atlas_real_case_uses_transported_trialx_and_case_local_hardware(
    tmp_path, repetition, case_kind,
):
    # The old FBLT result was produced with SplineEphem (source 2), but the
    # isolated integration case intentionally runs with SPICE (source 1).
    # Generate a bounded, deterministic seed in the actual test context so
    # this test measures Atlas transport rather than cross-ephemeris recovery.
    base_case = (
        ROOT / "testatron" / "tests" / "transcription_tests"
        / "MGAnDSMs_EMintercept.emtgopt"
    )
    evaluator = EMTGEvaluator(
        base_case=base_case,
        executable=ROOT / "bin" / "EMTGv9.exe",
        run_directory=tmp_path,
        timeout_seconds=30,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
        ephemeris_source_override=1,
    )
    options = evaluator.builder._base
    seed_document = json.loads(
        (
            ROOT / "tests" / "fixtures" / "atlas_mgandsm_spice_seed.json"
        ).read_text(encoding="utf-8")
    )
    assert float(seed_document["violation"]) <= float(
        seed_document["feasibility_tolerance"]
    )
    anchor = AtlasAnchor.from_options(
        options,
        evaluator.builder.hardware_path,
        metrics={
            "mission_events": (
                {
                    "julian_date_mjd": seed_document["launch_epoch"],
                    "c3": seed_document["departure_c3"],
                },
            ),
        },
    )
    registry = ParameterRegistry()
    materializer = HardwareVariantMaterializer(
        evaluator.builder.hardware_path, evaluator.artifact_store
    )

    genotype = random_genotype(
        GenomeSchema(SearchConfig(max_journeys=1, fixed_start="Earth", fixed_final="Mars")),
        random_stream(2, "atlas-integration"),
    )
    phase_type = int(options.Journeys[0].phase_type)
    impulses_per_phase = int(options.Journeys[0].impulses_per_phase)
    phenotype = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Earth", "Mars", (), {"phase_type": phase_type},
                (
                    PhasePhenotype(
                        "Mars",
                        {
                            "phase_type": phase_type,
                            "dsm_count": impulses_per_phase,
                        },
                    ),
                ),
            ),
        ),
    )
    record = CandidateRecord("atlas-real", genotype, phenotype, 0)
    targets = (
        {}
        if case_kind == "baseline"
        else _nearby_impulsive_targets(anchor)
    )
    plan = registry.plan_mutations(anchor, targets)
    prepared = materializer.prepare(
        options,
        plan,
        architecture_id=anchor.architecture.architecture_id,
    )
    candidate = atlas_candidate(record, prepared)
    source = TrialXSeed(
        "fixture-mgandsm-spice",
        tuple(map(str, seed_document["xdescriptions"])),
        tuple(map(float, seed_document["decision_vector"])),
        tuple(map(float, seed_document["xlowerbounds"])),
        tuple(map(float, seed_document["xupperbounds"])),
    )
    transported, _ = transport_trialx(source, targets)
    request = EvaluationRequest(
        candidate,
        "short",
        17,
        {
            "inner_loop": "nlp",
            "nlp_solver_type": 2,
            "feasibility_tolerance": 1.0e-8,
            "nlp_max_run_time": 20,
            "nlp_major_iterations": 2000,
        },
        transported.initial_guess(),
        {
            "evaluator": evaluator.context_identity(),
            "atlas_case_v1": prepared.to_dict(),
            "inner_seed_set": (17,),
        },
    )
    result = evaluator.evaluate(request)
    assert result.status is EvaluationStatus.FEASIBLE
    assert result.solver_violation is not None
    assert tuple(result.metrics["xdescriptions"]) == transported.xdescriptions
    assert result.metrics["departure_c3"] >= 0
    assert abs(
        float(result.metrics["departure_c3"])
        - float(result.metrics["mission_events"][0]["c3"])
    ) <= 1.0e-6
    assert Path(result.artifacts["options"]).is_file()


def _nearby_aeps_targets(anchor: AtlasAnchor) -> dict[str, Decimal]:
    discovered = ParameterRegistry().discover_mapping(anchor)
    factors = {
        MAXIMUM_MASS: Decimal("1.001"),
        ELECTRIC_PROPELLANT_CAPACITY: Decimal("1.001"),
        ENGINE_DUTY_CYCLE: Decimal("0.999"),
        BOL_POWER: Decimal("1.001"),
        CONSTANT_THRUST: Decimal("1.001"),
        THRUST_SCALE: Decimal("0.999"),
    }
    targets = {}
    for key, factor in factors.items():
        resolved = discovered[key]
        if not resolved.available:
            assert resolved.reason_code and resolved.reason
            continue
        assert resolved.current_value is not None
        current = Decimal(str(resolved.current_value))
        target = current * factor
        bounds = resolved.definition.hard_bounds.intersect(resolved.effective_bounds)
        if not bounds.contains(target):
            target = current * (Decimal(2) - factor)
        assert bounds.contains(target) and target != current
        targets[key] = target
    assert MAXIMUM_MASS in targets
    assert ENGINE_DUTY_CYCLE in targets
    assert BOL_POWER in targets
    assert (CONSTANT_THRUST in targets) != (THRUST_SCALE in targets)
    return targets


def test_aeps_real_matrix_hardware_preflight_is_complete(tmp_path):
    fixture = (
        ROOT / "testatron" / "tests" / "integration_asteroid_missions"
        / "A20136163_AEPS_IPOPT_FBLT.emtgopt"
    )
    hardware = ROOT / "testatron" / "HardwareModels"
    options = MissionOptions(str(fixture))
    assert options.success
    options.NLP_solver_type = 2
    options.NLP_feasibility_tolerance = 1.0e-8
    anchor = AtlasAnchor.from_options(options, hardware, metrics={})
    targets = _nearby_aeps_targets(anchor)
    plan = ParameterRegistry().plan_mutations(anchor, targets)
    prepared = HardwareVariantMaterializer(
        hardware, ArtifactStore(tmp_path / "artifacts"),
    ).prepare(options, plan, architecture_id=anchor.architecture.architecture_id)
    assert {mutation.parameter_key for mutation in plan.mutations} == set(targets)
    assert prepared.files
    assert prepared.architecture_id == anchor.architecture.architecture_id


def test_fblt_real_matrix_hardware_and_c3_preflight_is_complete(tmp_path):
    fixture = ROOT / "testatron" / "tests" / "transcription_tests" / "FBLT_EMintercept.emtgopt"
    hardware = ROOT / "testatron" / "HardwareModels"
    baseline = EMTGResultParser().parse(fixture.with_suffix(".emtg"))
    assert baseline.complete and baseline.feasible
    options = MissionOptions(str(fixture))
    anchor = AtlasAnchor.from_options(options, hardware, metrics=baseline.metrics)
    targets = _nearby_fblt_targets(anchor)
    plan = ParameterRegistry().plan_mutations(anchor, targets)
    prepared = HardwareVariantMaterializer(
        hardware, ArtifactStore(tmp_path / "artifacts"),
    ).prepare(options, plan, architecture_id=anchor.architecture.architecture_id)
    assert {LAUNCH_EPOCH, LAUNCH_C3_INPUT, MAXIMUM_MASS}.issubset(targets)
    assert {mutation.parameter_key for mutation in plan.mutations} == set(targets)
    assert prepared.launch_performance["c3_km2_s2"] == str(
        targets[LAUNCH_C3_INPUT].normalize()
    )


@pytest.mark.emtg_integration
@pytest.mark.outerloop_nightly
@pytest.mark.skipif(
    os.environ.get("EMTG_RUN_OUTERLOOP_INTEGRATION") != "1",
    reason="set EMTG_RUN_OUTERLOOP_INTEGRATION=1 for the AEPS Atlas matrix",
)
@pytest.mark.parametrize("repetition", range(2))
@pytest.mark.parametrize("case_kind", ["baseline", "nearby"])
def test_aeps_real_atlas_baseline_and_nearby_hardware_matrix(
    tmp_path, repetition, case_kind,
):
    fixture = (
        ROOT / "testatron" / "tests" / "integration_asteroid_missions"
        / "A20136163_AEPS_IPOPT_FBLT.emtgopt"
    )
    hardware = ROOT / "testatron" / "HardwareModels"
    executable = ROOT / "bin" / "EMTGv9.exe"
    assert fixture.is_file() and executable.is_file()
    options = MissionOptions(str(fixture))
    assert options.success
    options.NLP_solver_type = 2
    options.NLP_feasibility_tolerance = 1.0e-8
    anchor = AtlasAnchor.from_options(options, hardware, metrics={})
    requested = {} if case_kind == "baseline" else _nearby_aeps_targets(anchor)
    plan = ParameterRegistry().plan_mutations(anchor, requested)
    artifacts = ArtifactStore(tmp_path / "artifacts")
    prepared = HardwareVariantMaterializer(hardware, artifacts).prepare(
        options, plan, architecture_id=anchor.architecture.architecture_id,
    )

    case_directory = tmp_path / f"aeps-{case_kind}-{repetition}"
    case_directory.mkdir()
    apply_prepared_atlas_case(
        options, prepared.to_dict(), artifact_store_root=artifacts.root,
        case_directory=case_directory,
    )
    options.override_working_directory = 1
    options.forced_working_directory = case_directory.as_posix()
    options.override_mission_subfolder = 1
    options.forced_mission_subfolder = "."
    options.short_output_file_names = 1
    options.background_mode = 1
    options.universe_folder = (ROOT / "testatron" / "universe").as_posix()
    options.NLP_max_run_time = int(os.environ.get("EMTG_ATLAS_AEPS_BUDGET_SECONDS", "1200"))
    run_options = case_directory / fixture.name
    options.write_options_file(
        str(run_options), not options.print_only_non_default_options,
    )
    verify_written_atlas_case(run_options, prepared.to_dict())
    log = case_directory / "emtg_run.log"
    with log.open("w", encoding="utf-8") as stream:
        completed = subprocess.run(
            [str(executable), str(run_options)], stdout=stream,
            stderr=subprocess.STDOUT, check=False,
            timeout=options.NLP_max_run_time + 120,
        )
    assert completed.returncode == 0, log.read_text(encoding="utf-8", errors="replace")[-4000:]
    validate_output_dir(
        case_directory, read_mission_name(run_options), 1200.0, 1.0e-5,
    )


@pytest.mark.emtg_integration
@pytest.mark.skipif(
    os.environ.get("EMTG_RUN_OUTERLOOP_INTEGRATION") != "1",
    reason="set EMTG_RUN_OUTERLOOP_INTEGRATION=1 for the family continuation smoke test",
)
def test_real_emtg_one_step_maximum_mass_continuation_smoke(tmp_path):
    base_case = ROOT / "testatron" / "tests" / "transcription_tests" / "FBLT_EMintercept.emtgopt"
    evaluator = EMTGEvaluator(
        base_case=base_case,
        executable=ROOT / "bin" / "EMTGv9.exe",
        run_directory=tmp_path / "emtg",
        timeout_seconds=12,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
        ephemeris_source_override=1,
    )
    baseline = EMTGResultParser().parse(base_case.with_suffix(".emtg"))
    assert baseline.complete and baseline.feasible
    options = evaluator.builder._base
    anchor = AtlasAnchor.from_options(
        options, evaluator.builder.hardware_path, metrics=baseline.metrics
    )
    genotype = random_genotype(
        GenomeSchema(SearchConfig(max_journeys=1, fixed_start="Earth", fixed_final="Mars")),
        random_stream(3, "family-continuation-integration"),
    )
    phase_type = int(options.Journeys[0].phase_type)
    phenotype = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Earth", "Mars", (), {"phase_type": phase_type},
                (PhasePhenotype("Mars", {"phase_type": phase_type, "dsm_count": 0}),),
            ),
        ),
    )
    candidate = CandidateRecord("family-real", genotype, phenotype, 0)
    anchor_result = EvaluationResult(
        "family-real-anchor-evaluation",
        candidate.candidate_id,
        EvaluationStatus.FEASIBLE,
        "short",
        solver_violation=0.0,
        metrics={
            **baseline.metrics,
            "xdescriptions": baseline.xdescriptions,
            "decision_vector": baseline.decision_vector,
            "decision_vector_lower_bounds": baseline.xlowerbounds,
            "decision_vector_upper_bounds": baseline.xupperbounds,
        },
    )
    anchor_mass = Decimal(str(options.maximum_mass))
    family = FamilyDefinition(
        AtlasDefinition.create("Real continuation smoke").atlas_id,
        anchor.architecture.architecture_id,
        candidate.candidate_id,
        anchor_result.evaluation_key,
        ComparisonContext("family-real-smoke", 0, "short"),
        ParameterVector.from_mapping({MAXIMUM_MASS: anchor_mass}),
        (AxisDefinition(MAXIMUM_MASS, anchor_mass, anchor_mass + 1, Decimal("1")),),
        FeasibilityPolicyRef("real-smoke", 1),
        {"one_dimensional": {"growth_factor": 2}},
    )
    direct = {
        "inner_loop": "nlp",
        "nlp_solver_type": 2,
        "feasibility_tolerance": 1.0e-8,
        "nlp_max_run_time": 5,
        "nlp_major_iterations": 200,
    }
    policy = RescuePolicy(
        direct,
        {**direct, "nlp_max_run_time": 8, "nlp_major_iterations": 300},
        {
            "inner_loop": "mbh",
            "nlp_solver_type": 2,
            "feasibility_tolerance": 1.0e-8,
            "nlp_max_run_time": 5,
            "nlp_major_iterations": 200,
            "mbh_max_run_time": 5,
            "mbh_max_trials": 100,
        },
        alternate_seed_limit=0,
        mbh_confirmation_attempts=1,
    )
    executor = AtlasFamilyTaskExecutor(
        family,
        anchor,
        candidate,
        ParameterRegistry(),
        HardwareVariantMaterializer(
            evaluator.builder.hardware_path, evaluator.artifact_store
        ),
        FamilySampleEvaluator(
            evaluator,
            policy,
            cache=EvaluationCache(tmp_path / "shared-cache"),
        ),
    )
    worker = FamilyContinuationWorker(
        family,
        candidate,
        anchor_result,
        tmp_path / "family",
        executor,
        backend=LocalWorkerBackend(2),
    )
    outcome = worker.run()
    assert outcome.complete
    samples = worker.store.samples(completed_only=True)
    assert len(samples) == 2
    target = next(sample for sample in samples if sample.continuation_epoch == 1)
    assert target.parameters[MAXIMUM_MASS] == anchor_mass + 1
    assert target.classification in {
        SampleClassification.FEASIBLE_FOUND,
        SampleClassification.CONFIRMED_NOT_FOUND,
        SampleClassification.UNKNOWN,
    }
    attempts = worker.store.attempts(target.sample_key)
    assert attempts
    assert all(
        attempt["result"].status not in {
            EvaluationStatus.CONFIGURATION_FAILED,
            EvaluationStatus.INFRASTRUCTURE_FAILED,
            EvaluationStatus.EXECUTION_FAILED,
            EvaluationStatus.STRUCTURALLY_INVALID,
        }
        for attempt in attempts
    )
    assert any(
        Path(attempt["result"].artifacts["options"]).is_file()
        for attempt in attempts
        if "options" in attempt["result"].artifacts
    )
