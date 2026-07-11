from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
import sys
import time

import pytest

from OuterLoop.campaign import Campaign
from OuterLoop.config import CampaignConfig
from OuterLoop.evaluator import EMTGCaseBuilder, EMTGResultParser, SyntheticEvaluator
from OuterLoop.model import (
    CandidateRecord,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
)
from OuterLoop.genome import GenomeSchema, random_genotype
from OuterLoop.randomness import random_stream
from OuterLoop.seeds import (
    SeedArtifact,
    SeedInventory,
    SinglePhaseJourneyConverter,
    TPSLTToPSFBConverter,
    direct_compatibility,
    fingerprint_distance,
)
from OuterLoop.storage import ArtifactStore, CampaignStore, EvaluationCache
from OuterLoop.reporting import export_run, promote_candidate
from OuterLoop.process import run_process
from OuterLoop.qualification import qualify_exhaustively


ROOT = Path(__file__).resolve().parents[1]


def phenotype(*flybys: str, phase_type: int = 6) -> MissionPhenotype:
    phases = tuple(PhasePhenotype(target, {"phase_type": phase_type, "dsm_count": 1}) for target in (*flybys, "Mars"))
    return MissionPhenotype(
        {"launch_epoch": 60000},
        (JourneyPhenotype("Earth", "Mars", tuple(flybys), {"phase_type": phase_type}, phases),),
    )


def candidate(tmp_path: Path) -> tuple[CampaignConfig, CandidateRecord, EvaluationRequest]:
    config = CampaignConfig.from_dict(campaign_data(str(tmp_path / "run")), tmp_path / "campaign.json")
    genotype = random_genotype(GenomeSchema(config.search), random_stream(1, "genotype"))
    mission = phenotype("Venus")
    record = CandidateRecord("individual", genotype, mission, 0)
    request = EvaluationRequest(record, "full", 10, {}, None, {"binary": "one"})
    return config, record, request


def campaign_data(run_directory: str, *, workers: int = 2, generations: int = 3) -> dict:
    return {
        "schema_version": "outerloop/v2",
        "run_directory": run_directory,
        "root_seed": 314159,
        "search": {
            "max_journeys": 1,
            "min_journeys": 1,
            "max_flybys": 2,
            "fixed_start": "Earth",
            "fixed_final": "Mars",
            "flyby_bodies": ["Venus", "Earth"],
        },
        "objectives": ["emtg_objective", "delivered_mass"],
        "algorithm": {
            "population_size": 8,
            "generations": generations,
            "tournament_size": 2,
            "crossover_probability": 0.9,
            "mutation_probability": 0.8,
            "stall_generations": 20,
        },
        "evaluator": {
            "type": "synthetic",
            "problem": "architecture",
            "body_scores": {"Venus": 1.0, "Earth": 2.0, "Mars": 3.0},
            "base_mass": 1000.0,
        },
        "workers": {"count": workers, "infrastructure_retries": 1},
    }


def test_cache_is_content_addressed_and_context_sensitive(tmp_path):
    _, record, request = candidate(tmp_path)
    cache = EvaluationCache(tmp_path / "cache")
    result = EvaluationResult(
        request.evaluation_key,
        record.candidate_id,
        EvaluationStatus.FEASIBLE,
        "full",
        metrics={"emtg_objective": 1.0},
    )
    path = cache.put(result, request.context)
    assert path.is_file()
    assert cache.get(request.evaluation_key) == result
    changed = replace(request, context={"binary": "two"})
    assert changed.evaluation_key != request.evaluation_key
    explanation = cache.explain(changed.evaluation_key, record.candidate_id)
    assert not explanation["hit"]
    assert explanation["related_contexts"]


def test_campaign_store_recovers_partial_generation_atomically(tmp_path):
    _, record, request = candidate(tmp_path)
    store = CampaignStore(tmp_path / "run")
    store.save_candidates(0, 0, "parents", [record])
    assert store.load_candidates(0, 0, "parents")[0][1] is None
    result = EvaluationResult(request.evaluation_key, record.candidate_id, EvaluationStatus.EMTG_INFEASIBLE, "full", aggregate_violation=0.25)
    store.save_result(0, 0, "parents", 0, result)
    store.checkpoint({"status": "interrupted", "trial": 0, "generation": 0, "role": "parents"})
    assert store.load_candidates(0, 0, "parents")[0][1] == result
    assert CampaignStore(tmp_path / "run").load_checkpoint()["status"] == "interrupted"


def test_seed_compatibility_distance_and_selection():
    source = phenotype("Venus")
    target = phenotype("Venus", "Earth")
    seed = SeedArtifact.create("known.emtg", source, ["x", "y"], [1.0, 2.0], True, objective=5.0)
    assert not direct_compatibility(seed, target, ["x", "y"]).compatible
    assert direct_compatibility(seed, source, ["x", "y"]).compatible
    assert fingerprint_distance(seed.fingerprint, seed.fingerprint) == 0
    inventory = SeedInventory([seed])
    assert inventory.select(target)[0].seed_id == seed.seed_id


def test_safe_result_parser_classifies_truth_and_failure(tmp_path):
    parser = EMTGResultParser()
    truth = parser.parse(
        ROOT / "testatron" / "tests" / "global_mission_options" / "globalmissionoptions_MGALT_constrDryMass.emtg"
    )
    assert truth.complete and truth.feasible
    assert truth.metrics["flight_time"] == pytest.approx(302.0)
    assert truth.metrics["delivered_mass"] == pytest.approx(454.172284308)
    assert truth.metrics["total_propellant"] > 71.0
    assert len(truth.xdescriptions) == len(truth.decision_vector)
    failure_file = tmp_path / "FAILURE_case.emtg"
    failure_file.write_text(
        "J = 2.5\nWorst constraint is F[2]: test\nwith violation 0.125\n"
        "Solution attempt that produced a feasible solution with the best objective value (0 if no feasible solutions): 0\n",
        encoding="utf-8",
    )
    failure = parser.parse(failure_file, failure_file=True)
    assert failure.complete and not failure.feasible
    assert failure.violation == pytest.approx(0.125)


def test_case_builder_maps_logical_body_names_and_phase_data(tmp_path):
    source = ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt"
    builder = EMTGCaseBuilder(
        source,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
    )
    path = builder.build(
        phenotype("Venus"),
        tmp_path,
        "outer_case",
        evaluation_seed=12,
        budget={"inner_loop": "mbh", "mbh_max_run_time": 1, "nlp_solver_type": 2},
        initial_guess=None,
    )
    assert path.is_file()
    import MissionOptions

    options = MissionOptions.MissionOptions(str(path))
    assert options.Journeys[0].destination_list == [3, 4]
    assert options.Journeys[0].sequence == [2]
    assert options.Journeys[0].phase_type == 6
    assert options.Journeys[0].impulses_per_phase == 1
    assert options.forced_working_directory.replace("/", "\\") == str(tmp_path).replace("/", "\\")


def test_case_builder_expands_only_when_per_phase_genes_differ(tmp_path):
    source = ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt"
    builder = EMTGCaseBuilder(
        source,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
    )
    mixed = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Earth",
                "Mars",
                ("Venus",),
                {"phase_type": 6},
                (
                    PhasePhenotype("Venus", {"phase_type": 6, "dsm_count": 1}),
                    PhasePhenotype("Mars", {"phase_type": 7, "dsm_count": 0}),
                ),
            ),
        ),
    )
    path = builder.build(mixed, tmp_path, "expanded", evaluation_seed=1, budget={"inner_loop": "mbh"}, initial_guess=None)
    import MissionOptions

    options = MissionOptions.MissionOptions(str(path))
    assert len(options.Journeys) == 2
    assert options.Journeys[0].destination_list == [3, 2]
    assert options.Journeys[1].destination_list == [2, 4]
    assert options.Journeys[0].sequence == options.Journeys[1].sequence == []
    assert [journey.phase_type for journey in options.Journeys] == [6, 7]
    assert options.Journeys[1].departure_type == 3


def test_safe_process_runner_preserves_arguments_and_enforces_timeout(tmp_path):
    script = tmp_path / "echo argument.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "if sys.argv[1] == 'sleep': time.sleep(10)\n"
        "else: pathlib.Path(sys.argv[2]).write_text(sys.argv[1], encoding='utf-8')\n",
        encoding="utf-8",
    )
    marker = tmp_path / "marker.txt"
    argument = "value; $(not-a-command) & still-one-argument"
    outcome = run_process(
        [sys.executable, script, argument, marker],
        cwd=tmp_path,
        timeout_seconds=5,
        stdout_path=tmp_path / "stdout.log",
        stderr_path=tmp_path / "stderr.log",
    )
    assert outcome.returncode == 0
    assert marker.read_text(encoding="utf-8") == argument
    timed = run_process(
        [sys.executable, script, "sleep", marker],
        cwd=tmp_path,
        timeout_seconds=0.1,
        stdout_path=tmp_path / "timeout-out.log",
        stderr_path=tmp_path / "timeout-err.log",
    )
    assert timed.timed_out

    cancel_event = threading.Event()
    timer = threading.Timer(0.15, cancel_event.set)
    timer.start()
    try:
        cancelled = run_process(
            [sys.executable, script, "sleep", marker],
            cwd=tmp_path,
            timeout_seconds=5,
            stdout_path=tmp_path / "cancel-out.log",
            stderr_path=tmp_path / "cancel-err.log",
            cancel_event=cancel_event,
        )
    finally:
        timer.cancel()
    assert cancelled.cancelled and not cancelled.timed_out
    assert cancelled.runtime_seconds < 2.0


def test_atomic_cache_write_is_safe_under_same_key_contention(tmp_path):
    _, record, request = candidate(tmp_path)
    cache = EvaluationCache(tmp_path / "contended-cache")
    result = EvaluationResult(request.evaluation_key, record.candidate_id, EvaluationStatus.FEASIBLE, "full", metrics={"emtg_objective": 1.0})
    errors = []

    def write():
        try:
            cache.put(result, request.context)
        except Exception as error:  # pragma: no cover - asserted empty below
            errors.append(error)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert cache.get(request.evaluation_key) == result


def test_artifact_store_is_content_addressed_and_cache_conflicts_are_rejected(tmp_path):
    source = tmp_path / "solver.log"
    source.write_text("immutable\n", encoding="utf-8")
    stored, digest = ArtifactStore(tmp_path / "artifacts").put(source)
    source.write_text("changed\n", encoding="utf-8")
    assert stored.read_text(encoding="utf-8") == "immutable\n"
    assert digest in str(stored)

    _, record, request = candidate(tmp_path)
    cache = EvaluationCache(tmp_path / "conflict-cache")
    first = EvaluationResult(
        request.evaluation_key, record.candidate_id, EvaluationStatus.FEASIBLE,
        "full", metrics={"emtg_objective": 1.0},
    )
    cache.put(first, request.context)
    conflicting = replace(first, metrics={"emtg_objective": 2.0})
    with pytest.raises(ValueError, match="immutable cache conflict"):
        cache.put(conflicting, request.context)


def test_mission_options_user_data_accepts_literals_not_code():
    import MissionOptions

    assert MissionOptions._parse_user_data('(\"url\",\"a:b\"):(\"values\",[1,2])') == {
        "url": "a:b",
        "values": [1, 2],
    }
    with pytest.raises((ValueError, SyntaxError)):
        MissionOptions._parse_user_data('(\"bad\",__import__(\"os\").getcwd())')


class CountingEvaluator(SyntheticEvaluator):
    def __init__(self, settings):
        super().__init__(settings)
        self.count = 0
        self._lock = threading.Lock()

    def evaluate(self, request, cancel_event=None):
        with self._lock:
            self.count += 1
        return super().evaluate(request, cancel_event)


def test_campaign_resume_matches_uninterrupted_and_deduplicates(tmp_path):
    interrupted_config = CampaignConfig.from_dict(campaign_data(str(tmp_path / "interrupted"), workers=3), tmp_path / "campaign-a.json")
    evaluator = CountingEvaluator(interrupted_config.evaluator)
    first = Campaign(interrupted_config, evaluator=evaluator).run(max_new_evaluations=2)
    assert not first.complete and first.new_evaluations == 2
    resumed_evaluator = CountingEvaluator(interrupted_config.evaluator)
    resumed = Campaign(interrupted_config, evaluator=resumed_evaluator).run()
    assert resumed.complete

    full_config = CampaignConfig.from_dict(campaign_data(str(tmp_path / "full"), workers=1), tmp_path / "campaign-b.json")
    full_evaluator = CountingEvaluator(full_config.evaluator)
    uninterrupted = Campaign(full_config, evaluator=full_evaluator).run()
    assert uninterrupted.complete

    interrupted_store = CampaignStore(tmp_path / "interrupted")
    full_store = CampaignStore(tmp_path / "full")
    interrupted_rows = interrupted_store.load_candidates(resumed.trial, resumed.generation, "parents")
    full_rows = full_store.load_candidates(uninterrupted.trial, uninterrupted.generation, "parents")
    assert [candidate.candidate_id for candidate, _ in interrupted_rows] == [candidate.candidate_id for candidate, _ in full_rows]
    assert [result.metrics for _, result in interrupted_rows] == [result.metrics for _, result in full_rows]
    assert interrupted_store.status()["archive_count"] == full_store.status()["archive_count"]
    # Fewer actual calls than individual slots demonstrates phenotype/context deduplication.
    total_calls = evaluator.count + resumed_evaluator.count
    assert total_calls < interrupted_config.algorithm.population_size * (interrupted_config.algorithm.generations + 1)


def test_worker_count_does_not_change_campaign_result(tmp_path):
    one = CampaignConfig.from_dict(campaign_data(str(tmp_path / "one"), workers=1, generations=2), tmp_path / "one.json")
    four = CampaignConfig.from_dict(campaign_data(str(tmp_path / "four"), workers=4, generations=2), tmp_path / "four.json")
    one_outcome = Campaign(one).run()
    four_outcome = Campaign(four).run()
    one_rows = CampaignStore(tmp_path / "one").load_candidates(0, one_outcome.generation, "parents")
    four_rows = CampaignStore(tmp_path / "four").load_candidates(0, four_outcome.generation, "parents")
    assert [candidate.candidate_id for candidate, _ in one_rows] == [candidate.candidate_id for candidate, _ in four_rows]
    assert [result.metrics for _, result in one_rows] == [result.metrics for _, result in four_rows]


def test_resume_class_uses_resolved_configuration_not_mutable_source(tmp_path):
    config = CampaignConfig.from_dict(campaign_data(str(tmp_path / "resume"), generations=1), tmp_path / "source.json")
    partial = Campaign(config).run(max_new_evaluations=1)
    assert not partial.complete
    resumed = Campaign.resume(partial.checkpoint).run()
    assert resumed.complete


def test_fidelity_promotion_and_repeated_inner_trials_are_separate(tmp_path):
    data = campaign_data(str(tmp_path / "fidelity"), generations=0)
    data["evaluator"]["inner_trials"] = 3
    data["objectives"] = ["emtg_objective", "convergence_probability"]
    data["fidelities"] = [
        {"name": "short", "rank": 0, "budget": {"inner_loop": "mbh"}},
        {"name": "confirmed", "rank": 1, "budget": {"inner_loop": "nlp"}, "promote_count": 2},
    ]
    config = CampaignConfig.from_dict(data, tmp_path / "fidelity.json")
    outcome = Campaign(config).run()
    assert outcome.complete
    records = CampaignStore(tmp_path / "fidelity").archive_records()
    assert {record["fidelity"] for record in records} == {"short", "confirmed"}
    assert all("convergence_probability" in record["result"].metrics for record in records)
    checkpoint = CampaignStore(tmp_path / "fidelity").load_checkpoint()
    assert checkpoint["confirmed_fidelity"] == "confirmed"


def test_archives_are_partitioned_by_trial_and_comparison_context(tmp_path):
    data = campaign_data(str(tmp_path / "partitioned"), generations=0)
    data["algorithm"]["trials"] = 2
    config = CampaignConfig.from_dict(data, tmp_path / "partitioned.json")
    Campaign(config).run()
    records = CampaignStore(tmp_path / "partitioned").archive_records("full")
    assert {record["trial"] for record in records} == {0, 1}
    by_trial = {
        trial: {record["comparison_context_id"] for record in records if record["trial"] == trial}
        for trial in (0, 1)
    }
    assert all(len(values) == 1 for values in by_trial.values())
    assert by_trial[0] != by_trial[1]


def test_registered_outer_constraints_preserve_named_violation(tmp_path):
    data = campaign_data(str(tmp_path / "constraints"), generations=0)
    data["constraints"] = [
        {"name": "minimum_mass", "metric": "delivered_mass", "lower": 995.0, "scale": 5.0}
    ]
    config = CampaignConfig.from_dict(data, tmp_path / "constraints.json")
    outcome = Campaign(config).run()
    rows = CampaignStore(tmp_path / "constraints").load_candidates(0, outcome.generation, "parents")
    assert all(result.status is EvaluationStatus.OUTER_CONSTRAINT_INFEASIBLE for _, result in rows)
    assert all(result.constraints["minimum_mass"] > 0 for _, result in rows)
    assert all(result.aggregate_violation == pytest.approx(result.constraints["minimum_mass"] / 5.0) for _, result in rows)


def test_selected_objectives_are_typed_on_results_and_missing_values_are_classified(tmp_path):
    data = campaign_data(str(tmp_path / "objectives"), generations=0)
    config = CampaignConfig.from_dict(data, tmp_path / "objectives.json")
    outcome = Campaign(config).run()
    rows = CampaignStore(tmp_path / "objectives").load_candidates(0, outcome.generation, "parents")
    assert all(set(result.objectives) == {"emtg_objective", "delivered_mass"} for _, result in rows)
    assert all(all(value is not None for value in result.objectives.values()) for _, result in rows)

    missing_data = campaign_data(str(tmp_path / "missing-objective"), generations=0)
    missing_data["objectives"] = ["metric_that_does_not_exist"]
    missing_config = CampaignConfig.from_dict(missing_data, tmp_path / "missing.json")
    missing_outcome = Campaign(missing_config).run()
    missing_rows = CampaignStore(tmp_path / "missing-objective").load_candidates(
        0, missing_outcome.generation, "parents"
    )
    assert all(result.objectives == {"metric_that_does_not_exist": None} for _, result in missing_rows)
    assert all(result.status is EvaluationStatus.OUTPUT_INCOMPLETE for _, result in missing_rows)
    assert all("missing objective metrics" in result.failure_reason for _, result in missing_rows)

    penalty_data = campaign_data(str(tmp_path / "penalized-objective"), generations=0)
    penalty_data["objectives"] = [
        {"name": "metric_that_does_not_exist", "missing_policy": "penalize", "penalty": 123.0}
    ]
    penalty_config = CampaignConfig.from_dict(penalty_data, tmp_path / "penalty.json")
    penalty_outcome = Campaign(penalty_config).run()
    penalty_rows = CampaignStore(tmp_path / "penalized-objective").load_candidates(
        0, penalty_outcome.generation, "parents"
    )
    assert all(result.status is EvaluationStatus.FEASIBLE for _, result in penalty_rows)
    assert all(result.objectives["metric_that_does_not_exist"] == 123.0 for _, result in penalty_rows)


def test_modern_archive_warm_start_redecodes_and_reevaluates(tmp_path):
    source_data = campaign_data(str(tmp_path / "source-run"), generations=0)
    source_config = CampaignConfig.from_dict(source_data, tmp_path / "source.json")
    source_outcome = Campaign(source_config).run()
    exported = export_run(tmp_path / "source-run", legacy=False)

    warm_data = campaign_data(str(tmp_path / "warm-run"), generations=0)
    warm_data["seeds"] = {"warm_archive": exported["pareto_jsonl"]}
    warm_config = CampaignConfig.from_dict(warm_data, tmp_path / "warm.json")
    warm_outcome = Campaign(warm_config).run()
    rows = CampaignStore(tmp_path / "warm-run").load_candidates(0, warm_outcome.generation, "parents")
    assert any(candidate.operators == ("warm_start",) for candidate, _ in rows)
    assert all(result is not None for _, result in rows)


def test_promote_embeds_the_optimized_vector_in_a_standalone_case(tmp_path):
    from MissionOptions import MissionOptions

    _, record, request = candidate(tmp_path)
    source = ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt"
    parsed = MissionOptions(str(source))
    descriptions = [entry[0] for entry in parsed.trialX]
    vector = [float(entry[1]) for entry in parsed.trialX]
    result = EvaluationResult(
        request.evaluation_key,
        record.candidate_id,
        EvaluationStatus.FEASIBLE,
        "full",
        metrics={"xdescriptions": descriptions, "decision_vector": vector},
        artifacts={"options": str(source)},
    )
    store = CampaignStore(tmp_path / "promotion-run")
    store.save_candidates(0, 0, "parents", [record])
    store.save_result(0, 0, "parents", 0, result)
    output = promote_candidate(
        tmp_path / "promotion-run", record.candidate_id, allow_stale_context=True
    )
    promoted = MissionOptions(str(output))
    assert promoted.success == 1
    assert promoted.run_inner_loop == 0
    assert promoted.override_working_directory == 1
    assert Path(promoted.forced_working_directory).resolve() == output.parent
    assert [entry[0] for entry in promoted.trialX] == descriptions
    assert [float(entry[1]) for entry in promoted.trialX] == pytest.approx(vector)


def test_exhaustive_sequence_qualification_builds_canonical_truth(tmp_path):
    data = campaign_data(str(tmp_path / "exhaustive"), generations=0)
    config = CampaignConfig.from_dict(data, tmp_path / "exhaustive.json")
    campaign = Campaign(config)
    campaign.run()
    report = qualify_exhaustively(campaign, maximum_architectures=100)
    assert report.total_genotypes == 7
    assert report.unique_phenotypes == 7
    assert report.pareto_size == 1
    assert report.feasible == 7
    assert (
        tmp_path / "exhaustive" / "qualification" / "exhaustive-trial-0-full.json"
    ).is_file()


def test_external_physics_prefilter_protocol_is_enforced(tmp_path):
    provider = tmp_path / "provider.py"
    provider.write_text(
        "import json, pathlib, sys\n"
        "request = json.loads(pathlib.Path(sys.argv[-2]).read_text(encoding='utf-8'))\n"
        "assert request['schema_version'] == 3\n"
        "pathlib.Path(sys.argv[-1]).write_text(json.dumps({"
        "'schema_version': 3, 'accepted': False, 'reason': 'screened', "
        "'metrics': {'provider_cost': 7.0}}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    data = campaign_data(str(tmp_path / "provider-run"), generations=0)
    data["prefilters"] = [{
        "type": "lambert_provider",
        "strict": True,
        "heuristic": False,
        "audit_fraction": 0.0,
        "provider": {"command": [sys.executable, str(provider)], "timeout_seconds": 5},
    }]
    config = CampaignConfig.from_dict(data, tmp_path / "provider.json")
    outcome = Campaign(config).run()
    rows = CampaignStore(tmp_path / "provider-run").load_candidates(
        0, outcome.generation, "parents"
    )
    assert all(result.status is EvaluationStatus.STRICT_FILTERED for _, result in rows)
    assert all(result.metrics["provider_cost"] == 7.0 for _, result in rows)


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
@pytest.mark.filterwarnings("ignore::PendingDeprecationWarning")
def test_real_single_phase_and_tpslt_seed_converters():
    evm_options = ROOT / "docs" / "0_Users" / "tutorial" / "Tutorial_EMTG_Files" / "Flybys" / "EVM.emtgopt"
    source = phenotype("Venus", phase_type=6)
    target = MissionPhenotype(
        source.mission,
        (
            JourneyPhenotype("Earth", "Venus", (), {"phase_type": 6}, (PhasePhenotype("Venus", {"phase_type": 6, "dsm_count": 1}),)),
            JourneyPhenotype("Venus", "Mars", (), {"phase_type": 6}, (PhasePhenotype("Mars", {"phase_type": 6, "dsm_count": 1}),)),
        ),
    )
    split_seed = SeedArtifact.create(
        str(evm_options), source, ["placeholder"], [0.0], True,
        metadata={"options_path": str(evm_options)},
    )
    split = SinglePhaseJourneyConverter().convert(split_seed, target)
    assert len(split.xdescriptions) == len(split.decision_vector) == 33
    assert split.fingerprint.journey_count == 2

    prefix = ROOT / "testatron" / "tests" / "global_mission_options" / "globalmissionoptions_MGALT_obj0"
    mgalt_source = phenotype(phase_type=2)
    psfb_target = phenotype(phase_type=5)
    tpslt_seed = SeedArtifact.create(
        str(prefix) + ".emtg", mgalt_source, ["placeholder"], [0.0], True,
        metadata={
            "mission_path": str(prefix) + ".emtg",
            "options_path": str(prefix) + ".emtgopt",
        },
    )
    converted = TPSLTToPSFBConverter().convert(tpslt_seed, psfb_target)
    assert len(converted.xdescriptions) == len(converted.decision_vector) > 100
    assert all("MGALT" not in description for description in converted.xdescriptions)
