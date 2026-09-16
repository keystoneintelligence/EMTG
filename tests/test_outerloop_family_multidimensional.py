from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import math
import sqlite3

from OuterLoop.atlas import (
    AxisDefinition,
    BranchDefinition,
    FamilyAttempt,
    FamilyDefinition,
    FamilySample,
    FeasibilityPolicyRef,
    ParameterVector,
    SampleClassification,
)
from OuterLoop.atlas_evaluation import SampleEvaluationOutcome
from OuterLoop.family import FamilyContinuationWorker
from OuterLoop.family_multidimensional import (
    BoundaryProduct,
    MultidimensionalPlannerConfig,
    PairGrid,
    PairSliceDefinition,
    SearchPreset,
    branch_evidence,
    build_boundary_product,
    halton_directions,
    halton_point,
    radical_inverse,
)
from OuterLoop.family_storage import FamilyRunStore
from OuterLoop.model import (
    CandidateRecord,
    ComparisonContext,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
    Genotype,
    MissionPhenotype,
)
from OuterLoop.workers import LocalWorkerBackend


CONTEXT = ComparisonContext("multidimensional-context", 0, "full")


def candidate() -> CandidateRecord:
    return CandidateRecord("anchor", Genotype(), MissionPhenotype({}, ()), 0)


def metrics(x: float, y: float, branch_shift: float = 0.0):
    return {
        "xdescriptions": ("x", "y"),
        "decision_vector": (x + branch_shift, y + branch_shift),
        "decision_vector_lower_bounds": (-20.0, -20.0),
        "decision_vector_upper_bounds": (20.0, 20.0),
        "mission_events": ({
            "event_type": "launch",
            "location": "Earth",
            "julian_date_mjd": 60000.0,
            "position_km": (10000.0 + 10.0 * x + branch_shift, 10.0 * y, 0.0),
            "velocity_km_s": (1.0, 0.01 * y, 0.0),
            "mass": 1000.0 - x - y,
        },),
    }


def anchor_result(value: CandidateRecord, *, x: float = 0.0, y: float = 0.0):
    return EvaluationResult(
        "anchor-evaluation", value.candidate_id, EvaluationStatus.FEASIBLE, "full",
        solver_violation=0.0, metrics=metrics(x, y),
    )


def family(value: CandidateRecord, *, preset="balanced") -> FamilyDefinition:
    return FamilyDefinition(
        "atlas-22222222222222222222222222222222",
        "architecture-multidimensional-test",
        value.candidate_id,
        "anchor-evaluation",
        CONTEXT,
        ParameterVector.from_mapping({"x": 0, "y": 0}),
        (
            AxisDefinition("x", -4, 4, 1),
            AxisDefinition("y", -4, 4, 1),
        ),
        FeasibilityPolicyRef("test", 1),
        {
            "one_dimensional": {"growth_factor": 2},
            "multidimensional": {
                "preset": preset,
                "epoch_sample_limit": 8,
                "pair_sample_budget": 81,
                "pair_global_probe_count": 8,
                "pair_confirmation_budget": 32,
                "homogeneous_diagonal_target": "0.25",
            },
        },
    )


def deep_family(value: CandidateRecord) -> FamilyDefinition:
    return FamilyDefinition(
        "atlas-33333333333333333333333333333333",
        "architecture-deep-test",
        value.candidate_id,
        "anchor-evaluation",
        CONTEXT,
        ParameterVector.from_mapping({"x": 0, "y": 0, "z": 0}),
        tuple(AxisDefinition(key, -2, 2, 1) for key in ("x", "y", "z")),
        FeasibilityPolicyRef("test", 1),
        {
            "one_dimensional": {"growth_factor": 2},
            "multidimensional": {
                "preset": "deep",
                "epoch_sample_limit": 8,
                "pair_sample_budget": 25,
                "pair_global_probe_count": 4,
                "pair_confirmation_budget": 8,
                "homogeneous_diagonal_target": "0.5",
                "ray_sample_limit": 4,
            },
        },
    )


class ShapeExecutor:
    def __init__(self, value: CandidateRecord):
        self.candidate = value

    def context_identity(self):
        return {"type": "multidimensional-shape-v1"}

    def prepare(self, task):
        return {"sample_key": task.sample_key}

    def is_feasible(self, x: float, y: float) -> bool:
        return (x / 3.0) ** 2 + (y / 2.0) ** 2 <= 1.0

    def evaluate(self, task, prepared, feasible_seeds, *, cancel_event, attempt_observer):
        x = float(task.sample.parameters["x"])
        y = float(task.sample.parameters["y"])
        feasible = self.is_feasible(x, y)
        profile = str(task.task.get("evaluation_profile", "full"))
        conclusive = profile in {"full", "confirmation"}
        status = EvaluationStatus.FEASIBLE if feasible else EvaluationStatus.EMTG_INFEASIBLE
        ordinal = 1 if profile == "confirmation" else 0
        request = EvaluationRequest(
            self.candidate, "full", 1000 + ordinal,
            {"inner_loop": profile},
            {"xdescriptions": ["x", "y"], "decision_vector": [x, y]},
            {"type": "shape", "sample": task.sample_key, "profile": profile},
        )
        result = EvaluationResult(
            request.evaluation_key, self.candidate.candidate_id, status, "full",
            solver_violation=0.0 if feasible else 1.0, metrics=metrics(x, y),
        )
        attempt = FamilyAttempt(
            task.sample_key, result.evaluation_key, result.candidate_id, ordinal,
            feasible_seeds[0].result.evaluation_key if feasible_seeds else None,
            "full", request.budget, status, result.solver_violation,
        )
        attempt_observer(request, attempt, result, False)
        classification = (
            SampleClassification.FEASIBLE_FOUND if feasible
            else SampleClassification.CONFIRMED_NOT_FOUND if conclusive
            else SampleClassification.UNKNOWN
        )
        updated = replace(
            task.sample,
            classification=classification,
            confidence=1.0 if classification is not SampleClassification.UNKNOWN else None,
            best_evaluation_key=result.evaluation_key,
            metrics=result.metrics,
        )
        return SampleEvaluationOutcome(updated, (attempt,), (result,))


class AnnulusExecutor(ShapeExecutor):
    def is_feasible(self, x: float, y: float) -> bool:
        return 1.25 <= math.hypot(x, y) <= 3.75


class DisconnectedExecutor(ShapeExecutor):
    def is_feasible(self, x: float, y: float) -> bool:
        return min(math.hypot(x + 2, y), math.hypot(x - 2, y)) <= 1.5


def logical_state(worker: FamilyContinuationWorker):
    connection = sqlite3.connect(worker.store.database_path)
    try:
        epochs = connection.execute(
            "SELECT epoch,state,plan_hash,planned_count,completed_count FROM epochs ORDER BY epoch"
        ).fetchall()
    finally:
        connection.close()
    return {
        "samples": [item.to_dict() for item in worker.store.samples()],
        "epochs": epochs,
        "tasks": [
            (row["task_id"], row["sample_key"], row["epoch"], row["sequence"], row["purpose"], row["state"])
            for row in worker.store.multidimensional_task_rows()
        ],
        "cells": [item.to_dict() for item in worker.store.multidimensional_cells()],
        "rays": [item.to_dict() for item in worker.store.rays()],
        "edges": [item.to_dict() for item in worker.store.ready_edges()],
        "branches": [item.to_dict() for item in worker.store.branches()],
        "products": [
            (row["product_key"], row["sample_revision"], row["connectivity_revision"], row["content_hash"])
            for row in worker.store.latest_boundary_products()
        ],
    }


def test_low_discrepancy_contract_is_nested_and_antipodal():
    assert radical_inverse(1, 2) == Decimal("0.5")
    assert radical_inverse(2, 2) == Decimal("0.25")
    assert halton_point(1, 2) == (Decimal("0.5"), radical_inverse(1, 3))
    directions = halton_directions(8, 3)
    assert directions[:4] == halton_directions(4, 3)
    for first, second in zip(directions[::2], directions[1::2]):
        assert all(math.isclose(float(right), -float(left)) for left, right in zip(first, second))
        assert math.isclose(sum(float(item) ** 2 for item in first), 1.0)


def test_balanced_ellipse_produces_revisioned_conservative_product(tmp_path):
    anchor = candidate()
    worker = FamilyContinuationWorker(
        family(anchor), anchor, anchor_result(anchor), tmp_path / "ellipse",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(4),
    )
    outcome = worker.run()
    assert outcome.complete
    products = worker.store.latest_boundary_products()
    assert len(products) == 1
    product = products[0]["product"]
    assert product["source_sample_revision"] == worker.store.sample_set_revision
    assert product["source_connectivity_revision"] == worker.store.connectivity_revision
    assert product["coverage"]["evaluated_nodes"] > 9
    assert product["boundary_bands"]
    exact = {item["sample_key"]: item for item in product["exact_samples"]}
    assert all(item["authoritative"] for item in exact.values())
    assert all(not item["authoritative"] for item in product["predictions"])
    x_grid = PairGrid.from_family(worker.family, "x")
    y_grid = PairGrid.from_family(worker.family, "y")
    for component in product["feasible_components"]:
        for x_index, y_index in component["tiles"]:
            for x, y in (
                (x_index, y_index), (x_index + 1, y_index),
                (x_index, y_index + 1), (x_index + 1, y_index + 1),
            ):
                assert ShapeExecutor(anchor).is_feasible(
                    float(x_grid.value(x)), float(y_grid.value(y))
                )
    analytic = [
        (3.0 * math.cos(index * math.tau / 1440),
         2.0 * math.sin(index * math.tau / 1440))
        for index in range(1440)
    ]
    for x_index, y_index in product["boundary_bands"]:
        x0, x1 = float(x_grid.value(x_index)), float(x_grid.value(x_index + 1))
        y0, y1 = float(y_grid.value(y_index)), float(y_grid.value(y_index + 1))
        distance = min(
            math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))
            for x, y in analytic
        )
        assert distance <= math.hypot(x1 - x0, y1 - y0)


def test_multidimensional_config_requires_explicit_balanced_pairs_for_three_axes():
    anchor = candidate()
    value = family(anchor)
    value = replace(
        value,
        anchor_parameters=ParameterVector.from_mapping({"x": 0, "y": 0, "z": 0}),
        axes=(*value.axes, AxisDefinition("z", -1, 1, 1)),
    )
    try:
        MultidimensionalPlannerConfig.from_family(value)
    except ValueError as error:
        assert "requires selected_pairs" in str(error)
    else:
        raise AssertionError("balanced three-axis configuration should require selected pairs")


def test_pair_slice_keeps_non_axis_anchor_parameters_fixed(tmp_path):
    anchor = candidate()
    definition = replace(
        family(anchor),
        anchor_parameters=ParameterVector.from_mapping(
            {"x": 0, "y": 0, "fixed_input": 7}
        ),
    )
    worker = FamilyContinuationWorker(
        definition,
        anchor,
        anchor_result(anchor),
        tmp_path / "fixed-input",
        ShapeExecutor(anchor),
        backend=LocalWorkerBackend(1),
    )

    assert worker.multidimensional_planner.slices[0].fixed_parameters.as_mapping() == {
        "fixed_input": 7
    }


def test_deep_resume_and_worker_count_produce_identical_logical_state(tmp_path):
    anchor = candidate()
    definition = deep_family(anchor)
    one = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor), tmp_path / "one",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(1),
    )
    assert one.run().complete

    resumed = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor), tmp_path / "resumed",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(4),
    )
    partial = resumed.run(max_new_samples=13)
    assert not partial.complete
    resumed = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor), tmp_path / "resumed",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(2),
    )
    assert resumed.run().complete
    assert logical_state(one) == logical_state(resumed)


def _exhaustive_product(definition, predicate, component_for):
    config = MultidimensionalPlannerConfig.from_family(definition)
    slice_definition = PairSliceDefinition(
        definition.family_id, "x", "y", ParameterVector.from_mapping({})
    )
    x_grid, y_grid = PairGrid.from_family(definition, "x"), PairGrid.from_family(definition, "y")
    provisional = []
    for x_index in range(x_grid.size + 1):
        for y_index in range(y_grid.size + 1):
            x, y = float(x_grid.value(x_index)), float(y_grid.value(y_index))
            feasible = predicate(x, y)
            provisional.append((
                x, y,
                FamilySample(
                    definition.family_id, definition.architecture_id,
                    definition.comparison_context,
                    ParameterVector.from_mapping({"x": x_grid.value(x_index), "y": y_grid.value(y_index)}),
                    definition.comparison_context.fidelity,
                    classification=(
                        SampleClassification.FEASIBLE_FOUND if feasible
                        else SampleClassification.CONFIRMED_NOT_FOUND
                    ),
                    confidence=1.0,
                    best_evaluation_key=f"analytic-{x_index}-{y_index}",
                    metrics=metrics(x, y),
                ),
            ))
    roots = {}
    for x, y, sample in provisional:
        if sample.classification is SampleClassification.FEASIBLE_FOUND:
            roots.setdefault(component_for(x, y), sample.sample_key)
    branch_ids = {
        key: BranchDefinition(definition.family_id, root).branch_id
        for key, root in roots.items()
    }
    samples = tuple(
        replace(
            sample,
            branch_id=(
                branch_ids[component_for(x, y)]
                if sample.classification is SampleClassification.FEASIBLE_FOUND else None
            ),
        )
        for x, y, sample in provisional
    )
    return build_boundary_product(
        definition, slice_definition, samples,
        source_sample_revision=len(samples), source_connectivity_revision=1,
        config=config,
    ), samples


def test_annulus_product_preserves_a_clockwise_hole_ring():
    anchor = candidate()
    definition = family(anchor)
    product, _ = _exhaustive_product(
        definition,
        lambda x, y: 0.75 <= math.hypot(x, y) <= 3.75,
        lambda x, y: "annulus",
    )
    assert len(product.feasible_components) == 1
    rings = product.feasible_components[0]["rings"]
    assert len(rings) == 2

    def signed_area(ring):
        return sum(
            left[0] * right[1] - right[0] * left[1]
            for left, right in zip(ring, ring[1:])
        ) / 2

    areas = sorted(signed_area(ring) for ring in rings)
    assert areas[0] < 0 < areas[1]
    assert product.coverage.unresolved_area_fraction == 0.0


def test_global_probes_reveal_an_off_center_anchor_hole(tmp_path):
    anchor = candidate()
    definition = replace(
        family(anchor),
        anchor_parameters=ParameterVector.from_mapping({"x": 2, "y": 0}),
    )
    worker = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor, x=2), tmp_path / "annulus-worker",
        AnnulusExecutor(anchor), backend=LocalWorkerBackend(4),
    )
    assert worker.run().complete
    product = worker.store.latest_boundary_products()[0]["product"]
    assert product["coverage"]["global_probes_completed"] == 8
    center = next(
        item for item in worker.store.samples(completed_only=True)
        if item.parameters["x"] == 0 and item.parameters["y"] == 0
    )
    assert center.classification is SampleClassification.CONFIRMED_NOT_FOUND
    assert any(len(component["rings"]) >= 2 for component in product["feasible_components"])


def test_disconnected_components_are_never_bridged_by_tiles_or_rings():
    anchor = candidate()
    definition = family(anchor)
    product, _ = _exhaustive_product(
        definition,
        lambda x, y: min(math.hypot(x + 2, y), math.hypot(x - 2, y)) <= 1.5,
        lambda x, y: "left" if x < 0 else "right",
    )
    assert len(product.feasible_components) == 2
    assert len({item["branch_id"] for item in product.feasible_components}) == 2
    for component in product.feasible_components:
        x_values = {
            x for x, _ in component["tiles"]
        }
        assert max(x_values) < 4 or min(x_values) > 3
    occupied = [
        (component["component_id"], tuple(tile))
        for component in product.feasible_components for tile in component["tiles"]
    ]
    for first_id, (x, y) in occupied:
        for second_id, (other_x, other_y) in occupied:
            if first_id != second_id:
                assert abs(x - other_x) + abs(y - other_y) > 1


def test_global_probe_discovers_non_anchor_island_as_a_separate_branch(tmp_path):
    anchor = candidate()
    definition = replace(
        family(anchor),
        anchor_parameters=ParameterVector.from_mapping({"x": -2, "y": 0}),
    )
    worker = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor, x=-2), tmp_path / "islands-worker",
        DisconnectedExecutor(anchor), backend=LocalWorkerBackend(4),
    )
    assert worker.run().complete
    feasible = [
        item for item in worker.store.samples(completed_only=True)
        if item.classification is SampleClassification.FEASIBLE_FOUND
    ]
    assert any(item.parameters["x"] > 0 for item in feasible)
    assert len({item.branch_id for item in feasible}) >= 2
    product = worker.store.latest_boundary_products()[0]["product"]
    assert len(product["feasible_components"]) >= 2
    assert all(
        not (min(x for x, _ in component["tiles"]) < 4
             and max(x for x, _ in component["tiles"]) >= 4)
        for component in product["feasible_components"]
    )


def _branch_sample(*, shift=0.0, location="Earth", trajectory_jump=False):
    sample = FamilySample(
        "family-branch-evidence", "architecture-branch-evidence", CONTEXT,
        ParameterVector.from_mapping({"x": 0, "y": 0}), "full",
        classification=SampleClassification.FEASIBLE_FOUND,
    )
    sample_metrics = metrics(0, 0, shift)
    event = dict(sample_metrics["mission_events"][0])
    event["location"] = location
    if trajectory_jump:
        event["position_km"] = (100000.0, 0.0, 0.0)
    sample_metrics["mission_events"] = (event,)
    return replace(sample, metrics=sample_metrics)


def test_branch_split_requires_structure_or_both_numeric_signals():
    baseline = _branch_sample()
    structural = branch_evidence(baseline, _branch_sample(location="Mars"))
    decision_only = branch_evidence(baseline, _branch_sample(shift=30.0))
    trajectory_only = branch_evidence(baseline, _branch_sample(trajectory_jump=True))
    dual = branch_evidence(
        baseline, _branch_sample(shift=30.0, trajectory_jump=True)
    )
    assert structural["branch_split"]
    assert decision_only["decision_distance"] >= 0.5
    assert not decision_only["branch_split"]
    assert trajectory_only["trajectory_distance"] >= 0.25
    assert not trajectory_only["branch_split"]
    assert dual["branch_split"]


def test_exact_truth_suppresses_wrong_predictions_and_product_versions_are_idempotent(tmp_path):
    anchor = candidate()
    worker = FamilyContinuationWorker(
        family(anchor), anchor, anchor_result(anchor), tmp_path / "prediction",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(2),
    )
    assert worker.run().complete
    row = worker.store.latest_boundary_products()[0]
    product = BoundaryProduct.from_dict(row["product"])
    exact = product.exact_samples[0]
    wrong = replace(product, predictions=({
        "sample_key": exact["sample_key"],
        "grid_index": exact["grid_index"],
        "feasible_probability": 1.0,
        "source_sample_revision": product.source_sample_revision,
        "authoritative": False,
    },))
    assert wrong.predictions == ()
    before = worker.store.sample(exact["sample_key"]).to_dict()
    first = worker.store.save_boundary_product(product)
    second = worker.store.save_boundary_product(product)
    assert first == second
    assert worker.store.sample(exact["sample_key"]).to_dict() == before


def test_v1_store_backfill_preserves_axis_truth_and_plan_hashes(tmp_path):
    anchor = candidate()
    definition = replace(
        family(anchor, preset="quick"),
        planner_configuration={"one_dimensional": {"growth_factor": 2}},
    )
    worker = FamilyContinuationWorker(
        definition, anchor, anchor_result(anchor), tmp_path / "migration",
        ShapeExecutor(anchor), backend=LocalWorkerBackend(1),
    )
    assert worker.run().complete
    samples_before = [item.to_dict() for item in worker.store.samples()]
    attempts_before = [row["attempt"].to_dict() for row in worker.store.attempts()]
    connection = sqlite3.connect(worker.store.database_path)
    try:
        plans_before = connection.execute(
            "SELECT epoch,plan_hash FROM epochs ORDER BY epoch"
        ).fetchall()
        connection.execute("UPDATE metadata SET value='1' WHERE key='schema_version'")
        connection.execute(
            "DELETE FROM metadata WHERE key IN ('sample_set_revision','connectivity_revision')"
        )
        connection.commit()
    finally:
        connection.close()


    reopened = FamilyRunStore(worker.store.run_directory)
    assert reopened.get_metadata("schema_version") == 2
    assert reopened.sample_set_revision == len(samples_before)
    assert [item.to_dict() for item in reopened.samples()] == samples_before
    assert [row["attempt"].to_dict() for row in reopened.attempts()] == attempts_before
    connection = sqlite3.connect(reopened.database_path)
    try:
        assert connection.execute(
            "SELECT epoch,plan_hash FROM epochs ORDER BY epoch"
        ).fetchall() == plans_before
    finally:
        connection.close()
