from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import threading
import time

import pytest

from OuterLoop.atlas import (
    AxisDefinition,
    FamilyAttempt,
    FamilyDefinition,
    FeasibilityPolicyRef,
    ParameterVector,
    SampleClassification,
)
from OuterLoop.atlas_evaluation import SampleEvaluationOutcome
from OuterLoop.canonical import content_hash
from OuterLoop.family import FamilyContinuationWorker
from OuterLoop.control import RunDirective
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


CONTEXT = ComparisonContext("family-context", 0, "full")


def _candidate() -> CandidateRecord:
    return CandidateRecord(
        "anchor-individual",
        Genotype(),
        MissionPhenotype({}, ()),
        0,
    )


def _anchor_result(candidate: CandidateRecord) -> EvaluationResult:
    return EvaluationResult(
        "anchor-evaluation",
        candidate.candidate_id,
        EvaluationStatus.FEASIBLE,
        "full",
        solver_violation=0.0,
        metrics={
            "xdescriptions": ("decision",),
            "decision_vector": (0.0,),
            "decision_vector_lower_bounds": (-100.0,),
            "decision_vector_upper_bounds": (100.0,),
        },
    )


def _family(candidate: CandidateRecord, *, axes: tuple[AxisDefinition, ...] | None = None) -> FamilyDefinition:
    selected = axes or (AxisDefinition("x", -10, 10, Decimal("0.25")),)
    values = {axis.parameter_key: 0 for axis in selected}
    return FamilyDefinition(
        "atlas-11111111111111111111111111111111",
        "architecture-family-test",
        candidate.candidate_id,
        "anchor-evaluation",
        CONTEXT,
        ParameterVector.from_mapping(values),
        selected,
        FeasibilityPolicyRef("test", 1),
        {"one_dimensional": {"growth_factor": 2}},
    )


class IntervalExecutor:
    def __init__(self, candidate: CandidateRecord, intervals, *, delays=None):
        self.candidate = candidate
        self.intervals = dict(intervals)
        self.delays = dict(delays or {})
        self.calls = 0

    def context_identity(self):
        return {"type": "known-interval", "intervals": self.intervals}

    def prepare(self, task):
        return {"sample_key": task.sample_key}

    def evaluate(
        self, task, prepared, feasible_seeds, *, cancel_event, attempt_observer
    ):
        self.calls += 1
        axis_key = str(task.task["axis_key"])
        value = float(task.sample.parameters[axis_key])
        time.sleep(float(self.delays.get((axis_key, task.task["direction"]), 0.0)))
        lower, upper = self.intervals[axis_key]
        feasible = lower <= value <= upper
        status = EvaluationStatus.FEASIBLE if feasible else EvaluationStatus.EMTG_INFEASIBLE
        request = EvaluationRequest(
            self.candidate,
            "full",
            int(content_hash(task.sample_key, prefix="family-test-seed")[:7], 16),
            {"inner_loop": "nlp"},
            {
                "xdescriptions": ["decision"],
                "decision_vector": [value],
            },
            {"type": "known-interval", "sample_key": task.sample_key},
        )
        result = EvaluationResult(
            request.evaluation_key,
            self.candidate.candidate_id,
            status,
            "full",
            solver_violation=0.0 if feasible else 1.0,
            metrics={
                "xdescriptions": ("decision",),
                "decision_vector": (value,),
                "decision_vector_lower_bounds": (-100.0,),
                "decision_vector_upper_bounds": (100.0,),
            },
        )
        attempt = FamilyAttempt(
            task.sample_key,
            result.evaluation_key,
            result.candidate_id,
            0,
            feasible_seeds[0].result.evaluation_key,
            "full",
            request.budget,
            status,
            result.solver_violation,
        )
        attempt_observer(request, attempt, result, False)
        updated = replace(
            task.sample,
            classification=(
                SampleClassification.FEASIBLE_FOUND
                if feasible else SampleClassification.CONFIRMED_NOT_FOUND
            ),
            confidence=1.0,
            best_evaluation_key=result.evaluation_key,
            metrics=result.metrics,
        )
        return SampleEvaluationOutcome(updated, (attempt,), (result,))


class UnknownExecutor(IntervalExecutor):
    def evaluate(
        self, task, prepared, feasible_seeds, *, cancel_event, attempt_observer
    ):
        self.calls += 1
        return SampleEvaluationOutcome(
            replace(
                task.sample,
                classification=SampleClassification.UNKNOWN,
                confidence=None,
            ),
            (),
            (),
        )


class MutableRunControl:
    def __init__(self):
        self.paused = False
        self.cancelled = False
        self.cores = 2

    def snapshot(self):
        return RunDirective(self.cores, self.paused, self.cancelled)


class PausingExecutor(IntervalExecutor):
    def __init__(self, candidate, intervals, control):
        super().__init__(candidate, intervals)
        self.control = control
        self._guard = threading.Lock()
        self._did_pause = False

    def evaluate(self, *args, **kwargs):
        value = super().evaluate(*args, **kwargs)
        with self._guard:
            if not self._did_pause:
                self.control.paused = True
                self._did_pause = True
        return value


class CancellingExecutor(IntervalExecutor):
    def __init__(self, candidate, intervals, control):
        super().__init__(candidate, intervals)
        self.control = control
        self._guard = threading.Lock()
        self._did_cancel = False

    def evaluate(self, *args, **kwargs):
        value = super().evaluate(*args, **kwargs)
        with self._guard:
            if not self._did_cancel:
                self.control.cancelled = True
                self._did_cancel = True
        return value


class CrashAfterAttemptExecutor(IntervalExecutor):
    def __init__(self, candidate, intervals, *, cache_hit):
        super().__init__(candidate, intervals)
        self.cache_hit = cache_hit
        self._crashed = False

    def evaluate(
        self, task, prepared, feasible_seeds, *, cancel_event, attempt_observer
    ):
        def persist(request, attempt, result, _cache_hit):
            attempt_observer(request, attempt, result, self.cache_hit)

        outcome = super().evaluate(
            task, prepared, feasible_seeds, cancel_event=cancel_event,
            attempt_observer=persist,
        )
        if not self._crashed:
            self._crashed = True
            raise RuntimeError("qualification crash after durable attempt")
        return outcome


def _logical_state(worker: FamilyContinuationWorker):
    return {
        "samples": [
            (sample.sample_key, sample.continuation_epoch, sample.parent_sample_key,
             sample.classification.value, sample.parameters.to_dict())
            for sample in worker.store.samples()
        ],
        "chains": worker.store.chains(),
        "edges": [edge.to_dict() for edge in worker.store.ready_edges()],
    }


def test_known_interval_brackets_to_resolution(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    worker = FamilyContinuationWorker(
        family,
        candidate,
        _anchor_result(candidate),
        tmp_path / "family",
        IntervalExecutor(candidate, {"x": (-3.25, 4.75)}),
        backend=LocalWorkerBackend(3),
    )
    outcome = worker.run()
    assert outcome.complete
    chains = {chain.direction: chain for chain in worker.store.chains()}
    assert {chain.state for chain in chains.values()} == {"bracketed"}
    samples = {sample.sample_key: sample for sample in worker.store.samples()}
    assert samples[chains[-1].current_feasible_sample_key].parameters["x"] == Decimal("-3.25")
    assert samples[chains[-1].not_found_sample_key].parameters["x"] == Decimal("-3.5")
    assert samples[chains[1].current_feasible_sample_key].parameters["x"] == Decimal("4.75")
    assert samples[chains[1].not_found_sample_key].parameters["x"] == Decimal("5")
    assert worker.store.load_checkpoint()["status"] == "complete"


def test_scout_steps_start_at_one_resolution_and_double_on_success(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    worker = FamilyContinuationWorker(
        family,
        candidate,
        _anchor_result(candidate),
        tmp_path / "growth",
        IntervalExecutor(candidate, {"x": (-10, 10)}),
        backend=LocalWorkerBackend(2),
    )
    assert worker.run().complete
    values = {
        chain.direction: [
            sample.parameters["x"]
            for sample in worker.store.samples(completed_only=True)
            if sample.continuation_epoch > 0
            and (
                sample.parameters["x"] < 0
                if chain.direction < 0
                else sample.parameters["x"] > 0
            )
        ]
        for chain in worker.store.chains()
    }
    assert values[1] == [
        Decimal("0.25"), Decimal("0.75"), Decimal("1.75"),
        Decimal("3.75"), Decimal("7.75"), Decimal("10"),
    ]
    assert values[-1] == [
        Decimal("-0.25"), Decimal("-0.75"), Decimal("-1.75"),
        Decimal("-3.75"), Decimal("-7.75"), Decimal("-10"),
    ]


def test_worker_count_and_resume_do_not_change_global_results(tmp_path):
    candidate = _candidate()
    family = _family(candidate, axes=(
        AxisDefinition("x", -10, 10, Decimal("0.25")),
        AxisDefinition("y", -8, 8, Decimal("0.25")),
    ))
    intervals = {"x": (-3.25, 4.75), "y": (-2.0, 3.5)}
    one = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "one",
        IntervalExecutor(candidate, intervals), backend=LocalWorkerBackend(1),
    )
    assert one.run().complete

    interrupted_executor = IntervalExecutor(
        candidate, intervals,
        delays={("x", -1): 0.003, ("y", 1): 0.001},
    )
    interrupted = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "resumed",
        interrupted_executor, backend=LocalWorkerBackend(4),
    )
    partial = interrupted.run(max_new_samples=5)
    assert not partial.complete and partial.reason == "sample_budget"
    resumed = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "resumed",
        interrupted_executor, backend=LocalWorkerBackend(2),
    )
    assert resumed.run().complete
    assert _logical_state(one) == _logical_state(resumed)


def test_unknown_stops_each_direction_without_claiming_a_bracket(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    executor = UnknownExecutor(candidate, {"x": (-1, 1)})
    worker = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "unknown",
        executor, backend=LocalWorkerBackend(2),
    )
    assert worker.run().complete
    assert executor.calls == 2
    assert {chain.state for chain in worker.store.chains()} == {"blocked_unknown"}
    checkpoint = worker.store.load_checkpoint()
    assert checkpoint["conditional_ranges"]["x"]["negative"]["bracket"] is None
    assert checkpoint["conditional_ranges"]["x"]["positive"]["bracket"] is None


def test_soft_pause_finishes_only_the_current_bounded_batch_then_resumes(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    baseline = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "pause-baseline",
        IntervalExecutor(candidate, {"x": (-10, 10)}),
        backend=LocalWorkerBackend(1),
    )
    assert baseline.run().complete
    control = MutableRunControl()
    executor = PausingExecutor(candidate, {"x": (-10, 10)}, control)
    run_directory = tmp_path / "paused"
    worker = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        executor, backend=LocalWorkerBackend(2), run_control=control,
    )
    paused = worker.run()
    assert not paused.complete and paused.reason == "user_pause"
    assert executor.calls == 2
    assert worker.store.open_epoch() == 1
    assert worker.store.epoch_complete(1)
    assert worker.store.tasks_for_epoch(1, states=("running",)) == ()

    control.paused = False
    resumed = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        executor, backend=LocalWorkerBackend(1), run_control=control,
    )
    assert resumed.run().complete
    assert _logical_state(resumed) == _logical_state(baseline)


@pytest.mark.parametrize("cache_hit", [False, True])
def test_resume_after_attempt_or_cache_persistence_matches_uninterrupted_state(
    tmp_path, cache_hit,
):
    candidate = _candidate()
    family = _family(candidate)
    intervals = {"x": (-3.25, 4.75)}
    baseline = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / f"baseline-{cache_hit}",
        IntervalExecutor(candidate, intervals), backend=LocalWorkerBackend(1),
    )
    assert baseline.run().complete

    run_directory = tmp_path / f"restart-{cache_hit}"
    crashing = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        CrashAfterAttemptExecutor(candidate, intervals, cache_hit=cache_hit),
        backend=LocalWorkerBackend(1),
    )
    with pytest.raises(RuntimeError, match="durable attempt"):
        crashing.run()
    assert len([
        row for row in crashing.store.attempts() if row["request"] is not None
    ]) == 1

    resumed = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        IntervalExecutor(candidate, intervals), backend=LocalWorkerBackend(3),
    )
    assert resumed.run().complete
    assert _logical_state(resumed) == _logical_state(baseline)
    assert len(resumed.store.attempts()) == len(baseline.store.attempts())


def test_cancel_requeue_and_core_change_resume_matches_uninterrupted_state(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    intervals = {"x": (-3.25, 4.75)}
    baseline = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), tmp_path / "cancel-baseline",
        IntervalExecutor(candidate, intervals), backend=LocalWorkerBackend(1),
    )
    assert baseline.run().complete

    control = MutableRunControl()
    control.cores = 4
    run_directory = tmp_path / "cancelled"
    cancelled = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        CancellingExecutor(candidate, intervals, control),
        backend=LocalWorkerBackend(1), run_control=control,
    )
    outcome = cancelled.run()
    assert not outcome.complete and outcome.reason == "user_cancel"

    control.cancelled = False
    control.cores = 1
    resumed = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        IntervalExecutor(candidate, intervals),
        backend=LocalWorkerBackend(4), run_control=control,
    )
    assert resumed.run().complete
    assert resumed.backend.max_workers == 1
    assert _logical_state(resumed) == _logical_state(baseline)


def test_running_rows_are_requeued_after_an_abrupt_worker_exit(tmp_path):
    candidate = _candidate()
    family = _family(candidate)
    run_directory = tmp_path / "crash"
    executor = IntervalExecutor(candidate, {"x": (-1, 1)})
    interrupted = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        executor, backend=LocalWorkerBackend(2),
    )
    epoch = interrupted._plan_next_epoch()
    assert epoch == 1
    task = interrupted.store.tasks_for_epoch(epoch, states=("planned",))[0]
    interrupted.store.mark_running(task.sample_key, {"prepared": True})
    assert interrupted.store.sample_state(task.sample_key) == "running"

    resumed = FamilyContinuationWorker(
        family, candidate, _anchor_result(candidate), run_directory,
        executor, backend=LocalWorkerBackend(1),
    )
    partial = resumed.run(max_new_samples=1)
    assert not partial.complete and partial.reason == "sample_budget"
    assert resumed.store.sample_state(task.sample_key) == "completed"
    resumed.store.verify_integrity()
