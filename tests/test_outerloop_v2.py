from dataclasses import replace
import json
from pathlib import Path
import random
import sqlite3

import pytest

from OuterLoop.config import CampaignConfig, ConfigError, SearchConfig
from OuterLoop.evaluator import SyntheticEvaluator
from OuterLoop.genome import GenomeSchema, random_genotype, validate_genotype_structure
from OuterLoop.model import (
    CandidateRecord, EvaluationRequest, EvaluationResult, EvaluationStatus,
    JourneyPhenotype, MissionPhenotype, ScoredEvaluationResult,
)
from OuterLoop.operators import default_operator_registry
from OuterLoop.seeds import (
    SameShapeBodySubstitutionConverter, SeedArtifact, SinglePhaseJourneyConverter,
    TPSLTToPSFBConverter, default_converter_registry,
)
from OuterLoop.storage import CampaignStore, EvaluationCache
from OuterLoop.workers import FakeQueueBackend


def _phenotype(*flybys: str) -> MissionPhenotype:
    return MissionPhenotype(
        {"x0": 0.25, "x1": 0.0},
        (JourneyPhenotype("Earth", "Mars", tuple(flybys), {"phase_type": 6}),),
    )


def _config(tmp_path: Path, **changes):
    data = {
        "schema_version": "outerloop/v2",
        "run_directory": str(tmp_path / "run"),
        "search": {"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars"},
        "objectives": ["f1"],
        "evaluator": {"type": "synthetic", "problem": "zdt2"},
    }
    data.update(changes)
    return CampaignConfig.from_dict(data, tmp_path / "campaign.json")


def test_point_scores_and_derived_resonance_do_not_change_phenotype_identity():
    base = _phenotype("Venus")
    decorated = replace(
        base,
        point_group={"inner": {"score": 999.0}},
        resonance={"chains": [{"computed": "opportunity"}], "central_body": "Sun"},
    )
    assert decorated.identity == base.identity
    selected = replace(base, resonance={"selected": {"ratio": "2:1"}})
    assert selected.identity != base.identity


def test_evaluation_identity_uses_inner_seed_set_not_outer_trial():
    genotype = random_genotype(GenomeSchema(SearchConfig.from_dict({"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars"})), random.Random(1))
    left = CandidateRecord("a", genotype, _phenotype(), 0, trial=0)
    right = replace(left, individual_id="b", trial=99)
    context = {"inner_seed_set": (11, 12), "parser_version": 2}
    a = EvaluationRequest(left, "full", 11, context=context)
    b = EvaluationRequest(right, "full", 11, context=context)
    assert a.evaluation_key == b.evaluation_key


def test_cache_is_raw_only_and_explains_field_differences(tmp_path):
    cache = EvaluationCache(tmp_path / "cache")
    raw = EvaluationResult("key", "candidate", EvaluationStatus.FEASIBLE, "short")
    cache.put(raw, {"timeout": 10, "environment": {"A": "one"}})
    scored = ScoredEvaluationResult.from_raw(raw, campaign_feasible=True)
    with pytest.raises(TypeError, match="raw"):
        cache.put(scored, {})
    explanation = cache.explain(
        "different", "candidate", {"timeout": 20, "environment": {"A": "two"}}
    )
    fields = {item["field"] for item in explanation["related_contexts"][0]["field_differences"]}
    assert fields == {"environment.A", "timeout"}


@pytest.mark.parametrize("kind", ["campaign", "cache"])
def test_schema_one_state_is_rejected_without_rewrite(tmp_path, kind):
    root = tmp_path / kind
    root.mkdir()
    database = root / ("campaign.sqlite" if kind == "campaign" else "cache.sqlite")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO metadata VALUES('schema_version', '1')")
    before = database.read_bytes()
    constructor = CampaignStore if kind == "campaign" else EvaluationCache
    with pytest.raises(ValueError, match="fresh run/cache directory"):
        constructor(root)
    assert database.read_bytes() == before


@pytest.mark.parametrize(
    "change, message",
    [
        ({"prefilters": [{}]}, "type is required"),
        ({"operators": {"mutation": -1}}, "weights"),
        ({"seeds": {"external_provider": {"command": "shell text"}}}, "command"),
        ({"search": {"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars", "phase_genes": {"fidelity": {"kind": "choice", "choices": ["low", "high"]}}}}, "evaluation dimension"),
    ],
)
def test_strict_nested_configuration_rejects_unsafe_values(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        _config(tmp_path, **change)


def test_resolved_fixed_choice_genes_round_trip(tmp_path):
    config = _config(tmp_path, search={
        "max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars",
        "journey_genes": {"phase_type": {"kind": "fixed", "value": 6}},
    })
    resolved = config.resolved_dict()
    source = resolved.pop("source_path")
    restored = CampaignConfig.from_dict(resolved, source)
    assert restored.search.journey_genes["phase_type"].fixed == 6


def test_every_registered_operator_is_deterministic_and_structurally_valid(tmp_path):
    search = SearchConfig.from_dict({
        "max_journeys": 2, "min_journeys": 1, "max_flybys": 2,
        "fixed_start": "Earth", "fixed_final": "Mars", "flyby_bodies": ["Earth", "Venus"],
        "phase_genes": {"phase_type": {"kind": "choice", "choices": [6, 7]}},
    })
    schema = GenomeSchema(search)
    left = random_genotype(schema, random.Random(4))
    right = random_genotype(schema, random.Random(5))
    registry = default_operator_registry()
    for name in registry.names():
        operator = registry.get(name)
        if operator.mutation:
            first = operator.mutation(schema, left, random.Random(7))
            second = operator.mutation(schema, left, random.Random(7))
        else:
            first = operator.crossover(schema, left, right, random.Random(7))
            second = operator.crossover(schema, left, right, random.Random(7))
        assert first == second
        validate_genotype_structure(schema, first)


def test_same_shape_converter_reports_specific_mismatch():
    source = _phenotype("Venus")
    seed = SeedArtifact.create("source", source, ["x"], [1.0], True)
    converter = SameShapeBodySubstitutionConverter()
    mismatch = converter.compatibility(seed, _phenotype("Venus", "Earth"), ["x"])
    assert not mismatch.compatible
    assert "phase counts" in mismatch.reason
    assert default_converter_registry().get(converter.name).name == converter.name


@pytest.mark.parametrize("problem", ["zdt2", "zdt3"])
def test_zdt2_and_zdt3_synthetic_front_values(problem):
    schema = GenomeSchema(SearchConfig.from_dict({"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars"}))
    genotype = random_genotype(schema, random.Random(2))
    candidate = CandidateRecord("id", genotype, _phenotype(), 0)
    request = EvaluationRequest(candidate, "truth", 1)
    result = SyntheticEvaluator({"problem": problem}).evaluate(request)
    assert result.status is EvaluationStatus.FEASIBLE
    assert result.metrics["f1"] == pytest.approx(0.25)
    expected = 1.0 - 0.25**2 if problem == "zdt2" else 1.0 - 0.5 - 0.25
    assert result.metrics["f2"] == pytest.approx(expected)


def test_fake_distributed_backend_round_trips_in_request_order():
    schema = GenomeSchema(SearchConfig.from_dict({"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars"}))
    genotype = random_genotype(schema, random.Random(2))
    requests = []
    for index in range(3):
        candidate = CandidateRecord(str(index), genotype, _phenotype(), 0)
        requests.append(EvaluationRequest(candidate, "truth", index, context={"inner_seed_set": (index,)}))
    results = FakeQueueBackend().evaluate(requests, SyntheticEvaluator({"problem": "zdt2"}))
    assert [result.evaluation_key for result in results] == [request.evaluation_key for request in requests]


@pytest.mark.parametrize(
    "change, message",
    [
        ({"schema_version": "outerloop/v1"}, "outerloop/v2"),
        ({"workers": 4}, "workers must be an object"),
        ({"evaluator": {"type": "synthetic", "budget": {"mystery": 1}}}, "unknown evaluator.budget"),
        ({"search": {"max_journeys": 1, "fixed_start": "Earth", "fixed_final": "Mars", "mission_genes": {"flag": {"kind": "boolean"}}}}, "unsupported"),
    ],
)
def test_v2_contract_rejects_preproduction_and_untyped_shapes(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        _config(tmp_path, **change)


def test_transcription_converters_are_registered_and_reject_missing_artifacts():
    seed = SeedArtifact.create(
        "missing.emtg", _phenotype("Venus"), ["x"], [1.0], True,
        metadata={"options_path": "missing.emtgopt"},
    )
    for converter in (SinglePhaseJourneyConverter(), TPSLTToPSFBConverter()):
        result = converter.compatibility(seed, _phenotype("Venus"), ["x"])
        assert not result.compatible
        assert "unavailable" in result.reason
        assert default_converter_registry().get(converter.name).name == converter.name
