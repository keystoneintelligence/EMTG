from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
import math
import random

import pytest

from OuterLoop.canonical import CanonicalizationError, canonical_json
from OuterLoop.config import (
    CampaignConfig,
    ConfigError,
    GeneSpec,
    NSGA2Config,
    SearchConfig,
)
from OuterLoop.genome import GenomeSchema, decode_genotype, random_genotype
from OuterLoop.model import Genotype, HiddenGeneSlot, JourneyGenome
from OuterLoop.nsga2 import (
    NSGA2Individual,
    assign_crowding_distance,
    dominates,
    environmental_selection,
    exact_hypervolume_2d,
    fast_nondominated_sort,
    tournament_select,
)
from OuterLoop.operators import (
    activation_mutation,
    default_operator_registry,
    insertion_mutation,
    replacement_mutation,
)
from OuterLoop.randomness import derive_seed, random_stream


def schema() -> GenomeSchema:
    return GenomeSchema(
        SearchConfig(
            max_journeys=2,
            min_journeys=1,
            max_flybys=2,
            min_flybys=0,
            fixed_start="Earth",
            fixed_final="Mars",
            chain_journeys=True,
            mission_genes={
                "launch_epoch": GeneSpec("integer", lower=Decimal(60000), upper=Decimal(60002)),
            },
            journey_genes={
                "departure_destination": GeneSpec("choice", choices=("Earth", "Venus")),
                "arrival_destination": GeneSpec("choice", choices=("Venus", "Mars")),
                "phase_type": GeneSpec("choice", choices=(2, 6)),
            },
            phase_genes={
                "dsm_count": GeneSpec("integer", lower=Decimal(0), upper=Decimal(2)),
            },
            flyby_bodies=("Venus", "Earth"),
            repairs=("reconnect_endpoints",),
        )
    )


def explicit_genotype() -> Genotype:
    phases = (
        HiddenGeneSlot(True, {"dsm_count": 1}),
        HiddenGeneSlot(True, {"dsm_count": 2}),
        HiddenGeneSlot(True, {"dsm_count": 0}),
    )
    return Genotype(
        {"launch_epoch": 60000},
        (
            JourneyGenome(
                True,
                {"departure_destination": "Earth", "arrival_destination": "Mars", "phase_type": 6},
                (
                    HiddenGeneSlot(True, {"flyby_body": "Venus"}),
                    HiddenGeneSlot(False, {"flyby_body": "Earth"}),
                ),
                phases,
            ),
            JourneyGenome(
                False,
                {"departure_destination": "Venus", "arrival_destination": "Venus", "phase_type": 2},
                (
                    HiddenGeneSlot(True, {"flyby_body": "Earth"}),
                    HiddenGeneSlot(True, {"flyby_body": "Venus"}),
                ),
                phases,
            ),
        ),
    )


def test_canonical_json_rejects_nonfinite_and_is_order_independent():
    assert canonical_json({"b": 2, "a": 1.25}) == canonical_json({"a": 1.25, "b": 2})
    with pytest.raises(CanonicalizationError):
        canonical_json({"bad": math.nan})


def test_campaign_config_is_strict_and_resolves_paths(tmp_path):
    data = {
        "schema_version": "outerloop/v2",
        "run_directory": "run",
        "root_seed": 7,
        "search": {
            "max_journeys": 1,
            "fixed_start": "Earth",
            "fixed_final": "Mars",
            "journey_genes": {
                "arrival_destination": {"kind": "choice", "choices": ["Mars"]},
                "departure_destination": {"kind": "choice", "choices": ["Earth"]},
            },
        },
        "objectives": ["flight_time"],
    }
    source = tmp_path / "campaign.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    config = CampaignConfig.from_file(source)
    assert config.run_directory == (tmp_path / "run").resolve()
    assert not config.validate_paths()
    data["mystery"] = True
    with pytest.raises(ConfigError, match="unknown campaign"):
        CampaignConfig.from_dict(data, source)


def test_inactive_hidden_genes_are_neutral_and_deduplicate():
    left = explicit_genotype()
    hidden_flybys = list(left.journey_slots[0].flyby_slots)
    hidden_flybys[1] = HiddenGeneSlot(False, {"flyby_body": "Venus", "unused": 123})
    hidden_journey = replace(
        left.journey_slots[1],
        values={"departure_destination": "Earth", "arrival_destination": "Mars", "phase_type": 6},
    )
    right = replace(
        left,
        journey_slots=(
            replace(left.journey_slots[0], flyby_slots=tuple(hidden_flybys)),
            hidden_journey,
        ),
    )
    left_phenotype = decode_genotype(schema(), left)
    right_phenotype = decode_genotype(schema(), right)
    assert left_phenotype.identity == right_phenotype.identity
    assert left_phenotype.sequence_text == "Earth -> Venus -> Mars"
    assert len(left_phenotype.journeys[0].phases) == 2


def test_hgga_activation_changes_variable_length_but_retains_payload():
    genotype = explicit_genotype()
    journeys = list(genotype.journey_slots)
    journeys[1] = replace(journeys[1], active=True)
    activated = replace(genotype, journey_slots=tuple(journeys))
    phenotype = decode_genotype(schema(), activated)
    assert len(phenotype.journeys) == 2
    assert phenotype.journeys[1].departure == phenotype.journeys[0].arrival
    assert phenotype.journeys[-1].arrival == "Mars"
    assert activated.journey_slots[1].values["phase_type"] == 2
    assert any(
        repair.reason == "reconnect inherited journey endpoint"
        for repair in phenotype.repairs
    )


def test_random_genome_and_operators_are_reproducible():
    left = random_genotype(schema(), random.Random(2))
    right = random_genotype(schema(), random.Random(2))
    assert left == right
    assert activation_mutation(schema(), left, random.Random(3)) == activation_mutation(schema(), right, random.Random(3))
    assert insertion_mutation(schema(), left, random.Random(4)) == insertion_mutation(schema(), right, random.Random(4))
    assert replacement_mutation(schema(), explicit_genotype(), random.Random(5)) == replacement_mutation(schema(), explicit_genotype(), random.Random(5))
    names = default_operator_registry().names()
    assert {"activation", "insertion", "deletion", "journey_crossover", "phase_crossover"} <= set(names)


def test_seed_streams_are_coordinate_stable():
    assert derive_seed(7, "generation", 2, "slot", 3) == derive_seed(7, "generation", 2, "slot", 3)
    assert derive_seed(7, "generation", 2, "slot", 3) != derive_seed(7, "generation", 2, "slot", 4)
    assert random_stream(7, "x").random() == random_stream(7, "x").random()


def test_nondominated_sort_crowding_and_survival():
    population = [
        NSGA2Individual("a", (0.0, 1.0)),
        NSGA2Individual("b", (0.5, 0.5)),
        NSGA2Individual("c", (1.0, 0.0)),
        NSGA2Individual("d", (1.0, 1.0)),
    ]
    fronts = fast_nondominated_sort(population)
    assert [item.candidate_id for item in fronts[0]] == ["a", "b", "c"]
    assert [item.candidate_id for item in fronts[1]] == ["d"]
    crowded = {item.candidate_id: item.crowding_distance for item in assign_crowding_distance(fronts[0])}
    assert math.isinf(crowded["a"]) and math.isinf(crowded["c"])
    survivors = environmental_selection(population, 3)
    assert {item.candidate_id for item in survivors} == {"a", "b", "c"}
    assert exact_hypervolume_2d(survivors, (1.1, 1.1)) == pytest.approx(0.46)


def test_constraint_domination_preserves_infeasibility_distance():
    from OuterLoop.model import EvaluationStatus

    feasible = NSGA2Individual("feasible", (10.0,), EvaluationStatus.FEASIBLE, 0.0)
    near = NSGA2Individual("near", (0.0,), EvaluationStatus.EMTG_INFEASIBLE, 0.1)
    far = NSGA2Individual("far", (0.0,), EvaluationStatus.EMTG_INFEASIBLE, 1.0)
    assert dominates(feasible, near)
    assert dominates(near, far)
    assert not dominates(far, near)


def test_tournament_has_deterministic_identity_tie_break():
    population = [
        NSGA2Individual("b", (1.0,), rank=0, crowding_distance=1.0),
        NSGA2Individual("a", (1.0,), rank=0, crowding_distance=1.0),
    ]
    assert tournament_select(population, random.Random(0), 2).candidate_id == "a"


def test_zdt1_sample_is_nondominated_and_has_positive_hypervolume():
    samples = []
    for index in range(101):
        x = index / 100.0
        samples.append(NSGA2Individual(str(index), (x, 1.0 - math.sqrt(x))))
    assert len(fast_nondominated_sort(samples)) == 1
    assert exact_hypervolume_2d(samples, (1.1, 1.1)) > 0.85


def test_nsga2_recovers_a_zdt1_front_with_seeded_operators():
    rng = random.Random(2026)
    size = 48
    dimensions = 8

    def evaluate(vector, identifier):
        first = vector[0]
        g_value = 1.0 + 9.0 * sum(vector[1:]) / (dimensions - 1)
        return NSGA2Individual(identifier, (first, g_value * (1.0 - math.sqrt(first / g_value))), payload=vector)

    population = [evaluate([rng.random() for _ in range(dimensions)], f"initial-{index}") for index in range(size)]
    for generation in range(100):
        from OuterLoop.nsga2 import rank_population

        ranked = rank_population(population)
        offspring = []
        for slot in range(size):
            left = tournament_select(ranked, rng, 2).payload
            right = tournament_select(ranked, rng, 2).payload
            child = []
            for index, (a_value, b_value) in enumerate(zip(left, right)):
                alpha = rng.random()
                value = alpha * a_value + (1.0 - alpha) * b_value
                if rng.random() < 1.0 / dimensions:
                    value += rng.gauss(0.0, 0.08 if index == 0 else 0.12)
                child.append(max(0.0, min(1.0, value)))
            offspring.append(evaluate(child, f"g{generation}-{slot}"))
        population = environmental_selection([*population, *offspring], size)
    front = fast_nondominated_sort(population)[0]
    reference = [(index / 100.0, 1.0 - math.sqrt(index / 100.0)) for index in range(101)]
    igd = sum(min(math.dist(point, individual.objectives) for individual in front) for point in reference) / len(reference)
    assert igd < 0.08
    assert exact_hypervolume_2d(front, (1.1, 1.1)) > 0.82


@pytest.mark.parametrize("problem", ["zdt2", "zdt3"])
def test_nsga2_recovers_zdt2_and_zdt3_tradeoff_ranges(problem):
    rng = random.Random(700 if problem == "zdt2" else 701)
    size, dimensions = 48, 8

    def evaluate(vector, identifier):
        first = vector[0]
        g_value = 1.0 + 9.0 * sum(vector[1:]) / (dimensions - 1)
        if problem == "zdt2":
            second = g_value * (1.0 - (first / g_value) ** 2)
        else:
            ratio = first / g_value
            second = g_value * (1.0 - math.sqrt(ratio) - ratio * math.sin(10.0 * math.pi * first))
        return NSGA2Individual(identifier, (first, second), payload=(vector, g_value))

    population = [evaluate([rng.random() for _ in range(dimensions)], f"i-{index}") for index in range(size)]
    for generation in range(100):
        from OuterLoop.nsga2 import rank_population
        ranked = rank_population(population)
        offspring = []
        for slot in range(size):
            left = tournament_select(ranked, rng, 2).payload[0]
            right = tournament_select(ranked, rng, 2).payload[0]
            child = []
            for index, (a_value, b_value) in enumerate(zip(left, right)):
                alpha = rng.random()
                value = alpha * a_value + (1.0 - alpha) * b_value
                if rng.random() < 1.0 / dimensions:
                    value += rng.gauss(0.0, 0.08 if index == 0 else 0.12)
                child.append(max(0.0, min(1.0, value)))
            offspring.append(evaluate(child, f"g{generation}-{slot}"))
        population = environmental_selection([*population, *offspring], size)
    front = fast_nondominated_sort(population)[0]
    assert len(front) >= 8
    assert max(item.objectives[0] for item in front) - min(item.objectives[0] for item in front) > 0.6
    assert sorted(item.payload[1] for item in front)[len(front) // 2] < 1.15
