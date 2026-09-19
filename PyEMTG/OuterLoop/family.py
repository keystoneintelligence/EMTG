"""Deterministic one-dimensional continuation planning and execution."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext
import json
import os
from pathlib import Path
import time
import threading
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence

from .atlas import (
    AxisDefinition,
    ContinuationEdge,
    FamilyAttempt,
    FamilyDefinition,
    FamilySample,
    ParameterTransform,
    SampleClassification,
    decimal_value,
)
from .atlas_evaluation import (
    ContinuationSeed,
    FamilySampleEvaluator,
    HardwareVariantMaterializer,
    PreparedAtlasCase,
    SampleEvaluationOutcome,
)
from .canonical import content_hash, source_manifest
from .control import RunControl
from .family_storage import ChainState, FamilyRunStore, StoredTask
from .family_multidimensional import MultidimensionalPlannerConfig
from .family_multidimensional_worker import MultidimensionalEpochPlanner
from .model import CandidateRecord, EvaluationRequest, EvaluationResult, EvaluationStatus
from .parameters import AtlasAnchor, ParameterRegistry
from .serde import candidate_to_dict, result_from_dict, result_to_dict
from .storage import atomic_write_json
from .workers import LocalWorkerBackend, QueueRequest


FAMILY_RUN_CONFIG_SCHEMA = 1


@dataclass(frozen=True)
class OneDimensionalPlannerConfig:
    initial_step_resolution_units: int = 1
    growth_factor: Decimal = Decimal(2)
    unknown_policy: str = "stop"
    schema_version: int = 1

    def __post_init__(self) -> None:
        growth = decimal_value(self.growth_factor, where="growth_factor")
        object.__setattr__(self, "growth_factor", growth)
        if self.schema_version != 1:
            raise ValueError("unsupported one-dimensional planner schema")
        if self.initial_step_resolution_units < 1:
            raise ValueError("initial step resolution units must be positive")
        if growth <= 1:
            raise ValueError("continuation growth factor must exceed one")
        if self.unknown_policy != "stop":
            raise ValueError("Chunk 4 supports only unknown_policy='stop'")

    @classmethod
    def from_family(cls, family: FamilyDefinition) -> "OneDimensionalPlannerConfig":
        raw = family.planner_configuration.get("one_dimensional", {})
        if not isinstance(raw, Mapping):
            raise ValueError("planner_configuration.one_dimensional must be an object")
        allowed = {
            "schema_version", "initial_step_resolution_units", "growth_factor",
            "unknown_policy",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(
                "unknown one-dimensional planner field(s): " + ", ".join(unknown)
            )
        return cls(
            int(raw.get("initial_step_resolution_units", 1)),
            decimal_value(raw.get("growth_factor", 2), where="growth_factor"),
            str(raw.get("unknown_policy", "stop")),
            int(raw.get("schema_version", 1)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "initial_step_resolution_units": self.initial_step_resolution_units,
            "growth_factor": str(self.growth_factor),
            "unknown_policy": self.unknown_policy,
        }


@dataclass(frozen=True)
class AxisGrid:
    axis: AxisDefinition
    size: int
    anchor_index: int

    def value(self, index: int) -> Decimal:
        if not 0 <= index <= self.size:
            raise ValueError(f"axis grid index {index} is outside [0,{self.size}]")
        return self.axis.lower + self.axis.resolution * index

    def index(self, value: Any) -> int:
        selected = decimal_value(value, where=self.axis.parameter_key)
        raw = (selected - self.axis.lower) / self.axis.resolution
        integral = raw.to_integral_value()
        if raw != integral or not 0 <= int(integral) <= self.size:
            raise ValueError(
                f"{self.axis.parameter_key}={selected} is not on the configured axis grid"
            )
        return int(integral)

    def transform(self, value: Decimal) -> Decimal:
        if self.axis.transform is ParameterTransform.LINEAR:
            return value
        if value <= 0:
            raise ValueError(f"log axis {self.axis.parameter_key} requires positive values")
        with localcontext() as context:
            context.prec = 60
            return value.log10()

    def inverse(self, value: Decimal) -> Decimal:
        if self.axis.transform is ParameterTransform.LINEAR:
            return value
        with localcontext() as context:
            context.prec = 60
            return (value * Decimal(10).ln()).exp()

    def outward_index(self, desired_transform: Decimal, direction: int, current: int) -> int:
        desired = self.inverse(desired_transform)
        raw = (desired - self.axis.lower) / self.axis.resolution
        rounding = ROUND_CEILING if direction > 0 else ROUND_FLOOR
        proposed = int(raw.to_integral_value(rounding=rounding))
        proposed = max(current + 1, proposed) if direction > 0 else min(current - 1, proposed)
        return max(0, min(self.size, proposed))

    def midpoint_index(self, first: int, second: int) -> int | None:
        if abs(first - second) <= 1:
            return None
        midpoint = (self.transform(self.value(first)) + self.transform(self.value(second))) / 2
        native = self.inverse(midpoint)
        raw = (native - self.axis.lower) / self.axis.resolution
        candidates = {
            int(raw.to_integral_value(rounding=ROUND_FLOOR)),
            int(raw.to_integral_value(rounding=ROUND_CEILING)),
        }
        lower, upper = sorted((first, second))
        interior = [value for value in candidates if lower < value < upper]
        if not interior:
            interior = list(range(lower + 1, upper))
        return min(
            interior,
            key=lambda index: (
                abs(self.transform(self.value(index)) - midpoint),
                abs(index - self.anchor_index),
                index,
            ),
        )


class AxisContinuationPlanner:
    def __init__(
        self,
        family: FamilyDefinition,
        config: OneDimensionalPlannerConfig | None = None,
    ):
        self.family = family
        self.config = config or OneDimensionalPlannerConfig.from_family(family)
        self.grids: dict[str, AxisGrid] = {}
        for axis in family.axes:
            width = axis.upper - axis.lower
            raw_size = width / axis.resolution
            if raw_size != raw_size.to_integral_value():
                raise ValueError(
                    f"axis {axis.parameter_key} width must be an integral multiple of resolution"
                )
            if axis.transform is ParameterTransform.LOG10 and axis.lower <= 0:
                raise ValueError(f"log axis {axis.parameter_key} requires positive bounds")
            if axis.parameter_key not in family.anchor_parameters:
                raise ValueError(f"anchor is missing axis {axis.parameter_key}")
            grid = AxisGrid(axis, int(raw_size), 0)
            anchor_index = grid.index(family.anchor_parameters[axis.parameter_key])
            self.grids[axis.parameter_key] = AxisGrid(axis, grid.size, anchor_index)

    def initial_chains(self, anchor_sample_key: str) -> tuple[ChainState, ...]:
        output = []
        for axis_ordinal, axis in enumerate(self.family.axes):
            grid = self.grids[axis.parameter_key]
            for direction in (-1, 1):
                at_bound = grid.anchor_index == (0 if direction < 0 else grid.size)
                chain_id = content_hash(
                    {
                        "schema_version": 1,
                        "family_id": self.family.family_id,
                        "axis_key": axis.parameter_key,
                        "direction": direction,
                    },
                    prefix="emtg-family-axis-chain-v1",
                )
                output.append(ChainState(
                    chain_id, axis_ordinal, axis.parameter_key, direction,
                    axis_ordinal * 2 + (0 if direction < 0 else 1),
                    "bound_feasible" if at_bound else "scouting",
                    anchor_sample_key,
                    terminal_reason="anchor_at_axis_bound" if at_bound else None,
                ))
        return tuple(output)

    def _next_scout_index(
        self,
        chain: ChainState,
        current: FamilySample,
        previous: FamilySample | None,
    ) -> int:
        grid = self.grids[chain.axis_key]
        current_index = grid.index(current.parameters[chain.axis_key])
        if previous is None:
            step = self.config.initial_step_resolution_units
            return max(0, min(grid.size, current_index + chain.direction * step))
        previous_index = grid.index(previous.parameters[chain.axis_key])
        current_transform = grid.transform(grid.value(current_index))
        previous_transform = grid.transform(grid.value(previous_index))
        delta = abs(current_transform - previous_transform)
        desired = current_transform + chain.direction * self.config.growth_factor * delta
        return grid.outward_index(desired, chain.direction, current_index)

    def plan_chain(
        self,
        chain: ChainState,
        *,
        epoch: int,
        samples: Mapping[str, FamilySample],
        feasible_seed_keys: Sequence[str],
    ) -> tuple[StoredTask, ContinuationEdge] | None:
        if chain.terminal:
            return None
        grid = self.grids[chain.axis_key]
        current = samples[chain.current_feasible_sample_key]
        current_index = grid.index(current.parameters[chain.axis_key])
        if chain.state == "scouting":
            previous = (
                None if chain.previous_feasible_sample_key is None
                else samples[chain.previous_feasible_sample_key]
            )
            target_index = self._next_scout_index(chain, current, previous)
            if target_index == current_index:
                raise RuntimeError(f"nonterminal chain {chain.chain_id} cannot advance")
            purpose = "scout"
            chain_sources = [chain.current_feasible_sample_key]
            if chain.previous_feasible_sample_key is not None:
                chain_sources.append(chain.previous_feasible_sample_key)
        elif chain.state == "refining":
            if chain.not_found_sample_key is None:
                raise RuntimeError("refining chain has no confirmed-not-found endpoint")
            not_found = samples[chain.not_found_sample_key]
            not_found_index = grid.index(not_found.parameters[chain.axis_key])
            target = grid.midpoint_index(current_index, not_found_index)
            if target is None:
                raise RuntimeError("adjacent bracket should already be terminal")
            target_index = target
            purpose = "refine"
            chain_sources = [chain.current_feasible_sample_key]
        else:
            raise RuntimeError(f"unsupported active chain state {chain.state}")

        parameters = self.family.anchor_parameters.with_values({
            chain.axis_key: grid.value(target_index)
        })
        sample = FamilySample(
            self.family.family_id,
            self.family.architecture_id,
            self.family.comparison_context,
            parameters,
            self.family.comparison_context.fidelity,
            parent_sample_key=current.sample_key,
            continuation_epoch=epoch,
        )
        task_document = {
            "schema_version": 1,
            "chain_id": chain.chain_id,
            "axis_key": chain.axis_key,
            "axis_ordinal": chain.axis_ordinal,
            "direction": chain.direction,
            "purpose": purpose,
            "grid_index": target_index,
            "chain_source_keys": chain_sources,
            "feasible_seed_keys": list(feasible_seed_keys),
        }
        edge = ContinuationEdge(
            self.family.family_id,
            current.sample_key,
            sample.sample_key,
            f"axis_{purpose}",
            (
                abs(
                    grid.transform(grid.value(target_index))
                    - grid.transform(grid.value(current_index))
                )
                / abs(
                    grid.transform(grid.axis.upper)
                    - grid.transform(grid.axis.lower)
                )
            ),
            {
                "axis_key": chain.axis_key,
                "direction": chain.direction,
                "purpose": purpose,
                "parent_classification": SampleClassification.FEASIBLE_FOUND.value,
                "child_classification": "planned",
                "connected_feasible": False,
            },
        )
        return (
            StoredTask(
                sample, chain.chain_id, epoch, chain.rank, purpose, target_index,
                task_document,
            ),
            edge,
        )

    def finalize_edge(
        self, edge: ContinuationEdge, sample: FamilySample
    ) -> ContinuationEdge:
        return replace(edge, branch_evidence={
            **dict(edge.branch_evidence),
            "child_classification": sample.classification.value,
            "connected_feasible": (
                sample.classification is SampleClassification.FEASIBLE_FOUND
            ),
        })

    def apply_outcome(
        self, chain: ChainState, task: StoredTask, sample: FamilySample
    ) -> ChainState:
        grid = self.grids[chain.axis_key]
        classification = sample.classification
        if classification is SampleClassification.UNKNOWN:
            return replace(
                chain, state="blocked_unknown", terminal_reason="unknown_sample"
            )
        if task.purpose == "scout":
            if classification is SampleClassification.FEASIBLE_FOUND:
                at_bound = task.grid_index == (0 if chain.direction < 0 else grid.size)
                return replace(
                    chain,
                    state="bound_feasible" if at_bound else "scouting",
                    previous_feasible_sample_key=chain.current_feasible_sample_key,
                    current_feasible_sample_key=sample.sample_key,
                    successful_scouts=chain.successful_scouts + 1,
                    terminal_reason="axis_bound_feasible" if at_bound else None,
                )
            if classification is SampleClassification.CONFIRMED_NOT_FOUND:
                return replace(
                    chain,
                    state="refining",
                    not_found_sample_key=sample.sample_key,
                    terminal_reason=None,
                )
        elif task.purpose == "refine":
            if chain.not_found_sample_key is None:
                raise RuntimeError("refinement result has no not-found endpoint")
            if classification is SampleClassification.FEASIBLE_FOUND:
                updated = replace(
                    chain,
                    current_feasible_sample_key=sample.sample_key,
                    refinement_count=chain.refinement_count + 1,
                )
            elif classification is SampleClassification.CONFIRMED_NOT_FOUND:
                updated = replace(
                    chain,
                    not_found_sample_key=sample.sample_key,
                    refinement_count=chain.refinement_count + 1,
                )
            else:
                raise RuntimeError("unexpected refinement classification")
            return updated
        raise RuntimeError(
            f"unexpected {classification.value} result for {task.purpose} task"
        )

    def finalize_bracket_state(
        self, chain: ChainState, samples: Mapping[str, FamilySample]
    ) -> ChainState:
        if chain.state != "refining" or chain.not_found_sample_key is None:
            return chain
        grid = self.grids[chain.axis_key]
        feasible_index = grid.index(
            samples[chain.current_feasible_sample_key].parameters[chain.axis_key]
        )
        not_found_index = grid.index(
            samples[chain.not_found_sample_key].parameters[chain.axis_key]
        )
        if abs(feasible_index - not_found_index) <= 1:
            return replace(
                chain, state="bracketed", terminal_reason="resolution_reached"
            )
        return chain

    def range_summary(
        self, chains: Sequence[ChainState], samples: Mapping[str, FamilySample]
    ) -> dict[str, Any]:
        by_axis = {(chain.axis_key, chain.direction): chain for chain in chains}
        anchor = self.family.anchor_parameters.as_mapping()
        output: dict[str, Any] = {}
        for axis in self.family.axes:
            negative = by_axis[(axis.parameter_key, -1)]
            positive = by_axis[(axis.parameter_key, 1)]

            def direction_value(chain: ChainState) -> dict[str, Any]:
                feasible = samples[chain.current_feasible_sample_key]
                feasible_value = feasible.parameters[axis.parameter_key]
                bracket = None
                if chain.not_found_sample_key is not None:
                    not_found = samples[chain.not_found_sample_key]
                    not_found_value = not_found.parameters[axis.parameter_key]
                    bracket = {
                        "feasible_sample_key": feasible.sample_key,
                        "feasible_value": str(feasible_value),
                        "confirmed_not_found_sample_key": not_found.sample_key,
                        "confirmed_not_found_value": str(not_found_value),
                        "width": str(abs(decimal_value(feasible_value) - decimal_value(not_found_value))),
                        "resolution": str(axis.resolution),
                        "complete": chain.state == "bracketed",
                    }
                return {
                    "state": chain.state,
                    "terminal_reason": chain.terminal_reason,
                    "last_feasible_sample_key": feasible.sample_key,
                    "last_feasible_value": str(feasible_value),
                    "bracket": bracket,
                }

            negative_value = samples[negative.current_feasible_sample_key].parameters[axis.parameter_key]
            positive_value = samples[positive.current_feasible_sample_key].parameters[axis.parameter_key]
            conditioned = {
                key: (value if isinstance(value, int) else str(value))
                for key, value in anchor.items() if key != axis.parameter_key
            }
            output[axis.parameter_key] = {
                "found_feasible_lower": str(negative_value),
                "found_feasible_upper": str(positive_value),
                "conditioned_on_anchor": conditioned,
                "negative": direction_value(negative),
                "positive": direction_value(positive),
            }
        return output
class FamilyTaskExecutor(Protocol):
    def context_identity(self) -> Mapping[str, Any]: ...

    def prepare(self, task: StoredTask) -> Any: ...

    def evaluate(
        self,
        task: StoredTask,
        prepared: Any,
        feasible_seeds: Sequence[ContinuationSeed],
        *,
        cancel_event: threading.Event,
        attempt_observer: Callable[
            [EvaluationRequest, FamilyAttempt, EvaluationResult, bool], None
        ],
    ) -> SampleEvaluationOutcome: ...


class AtlasFamilyTaskExecutor:
    """Bind the pure planner to the accepted Chunk 3 case/evaluation layer."""

    def __init__(
        self,
        family: FamilyDefinition,
        anchor: AtlasAnchor,
        anchor_candidate: CandidateRecord,
        registry: ParameterRegistry,
        materializer: HardwareVariantMaterializer,
        evaluator: FamilySampleEvaluator,
    ):
        self.family = family
        self.anchor = anchor
        self.anchor_candidate = anchor_candidate
        self.registry = registry
        self.materializer = materializer
        self.evaluator = evaluator
        discovered = registry.discover_mapping(anchor)
        for axis in family.axes:
            registry.validate_axis(discovered[axis.parameter_key], axis)

    def context_identity(self) -> Mapping[str, Any]:
        return {
            "type": "atlas_one_dimensional_task_executor",
            "schema_version": 1,
            "family_id": self.family.family_id,
            "evaluator": self.evaluator.context_identity(),
        }

    def prepare(self, task: StoredTask) -> PreparedAtlasCase:
        axis_keys = tuple(task.task.get("axis_keys", ()))
        if not axis_keys and task.task.get("axis_key") is not None:
            axis_keys = (str(task.task["axis_key"]),)
        if not axis_keys and task.task.get("axis_ordinal") is not None:
            axis_keys = (self.family.axes[int(task.task["axis_ordinal"])].parameter_key,)
        axes_by_key = {axis.parameter_key: axis for axis in self.family.axes}
        selected_axes = tuple(axes_by_key[key] for key in axis_keys)
        requested = {
            key: task.sample.parameters[key]
            for key in axis_keys
            if task.sample.parameters[key] != self.family.anchor_parameters[key]
        }
        plan = self.registry.plan_mutations(
            self.anchor,
            requested,
            axes=selected_axes,
        )
        return self.materializer.prepare(
            self.anchor.options,
            plan,
            architecture_id=self.family.architecture_id,
        )

    def evaluate(
        self,
        task: StoredTask,
        prepared: PreparedAtlasCase,
        feasible_seeds: Sequence[ContinuationSeed],
        *,
        cancel_event: threading.Event,
        attempt_observer: Callable[
            [EvaluationRequest, FamilyAttempt, EvaluationResult, bool], None
        ],
    ) -> SampleEvaluationOutcome:
        axis_key = task.task.get("axis_key")
        if axis_key is None and task.task.get("axis_ordinal") is not None:
            axis_key = self.family.axes[int(task.task["axis_ordinal"])].parameter_key
        axes_by_key = {axis.parameter_key: axis for axis in self.family.axes}
        normalization = {
            axis.parameter_key: float(axis.upper - axis.lower)
            for axis in self.family.axes
        }
        return self.evaluator.evaluate(
            task.sample,
            self.anchor_candidate,
            prepared,
            feasible_seeds,
            chain_source_keys=tuple(task.task.get("chain_source_keys", ())),
            axis_key=None if axis_key is None else str(axis_key),
            axis_transform=(
                ParameterTransform.LINEAR
                if axis_key is None else axes_by_key[str(axis_key)].transform
            ),
            secant_coordinates=(
                None
                if task.task.get("radial_coordinate") is None
                else tuple(task.task["radial_coordinate"])
            ),
            normalization=normalization,
            evaluation_profile=str(task.task.get("evaluation_profile", "full")),
            previous_attempts=tuple(
                FamilyAttempt.from_dict(item)
                for item in task.task.get("previous_attempts", ())
            ),
            previous_results=tuple(
                result_from_dict(item)
                for item in task.task.get("previous_results", ())
            ),
            cancel_event=cancel_event,
            attempt_observer=attempt_observer,
        )


@dataclass(frozen=True)
class FamilyProgress:
    status: str
    reason: str | None
    open_epoch: int | None
    last_closed_epoch: int
    effective_workers: int
    total_chains: int
    terminal_chains: int
    chain_states: Mapping[str, int]
    sample_counts: Mapping[str, int]
    conditional_ranges: Mapping[str, Any]
    multidimensional: Mapping[str, Any] | None = None

    @property
    def fraction(self) -> float:
        return 1.0 if self.total_chains == 0 else self.terminal_chains / self.total_chains

    def to_dict(self) -> dict[str, Any]:
        output = {
            "status": self.status,
            "reason": self.reason,
            "open_epoch": self.open_epoch,
            "last_closed_epoch": self.last_closed_epoch,
            "effective_workers": self.effective_workers,
            "total_chains": self.total_chains,
            "terminal_chains": self.terminal_chains,
            "fraction": self.fraction,
            "chain_states": dict(self.chain_states),
            "sample_counts": dict(self.sample_counts),
            "conditional_ranges": dict(self.conditional_ranges),
        }
        if self.multidimensional is not None:
            output["multidimensional"] = dict(self.multidimensional)
        return output


class FamilyObserver(Protocol):
    def on_progress(self, progress: FamilyProgress) -> None: ...
    def on_attempt(self, sample_key: str, attempt: FamilyAttempt, cache_hit: bool) -> None: ...
    def on_sample(self, sample: FamilySample) -> None: ...


class NullFamilyObserver:
    def on_progress(self, progress: FamilyProgress) -> None:
        return None

    def on_attempt(self, sample_key: str, attempt: FamilyAttempt, cache_hit: bool) -> None:
        return None

    def on_sample(self, sample: FamilySample) -> None:
        return None


@dataclass(frozen=True)
class FamilyRunOutcome:
    complete: bool
    family_id: str
    open_epoch: int | None
    last_closed_epoch: int
    new_samples: int
    terminal_chains: int
    total_chains: int
    checkpoint: str
    reason: str | None = None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def _family_run_lock(path: Path, timeout_seconds: float = 30.0) -> Iterator[None]:
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        except FileExistsError:
            try:
                owner = int(path.read_text(encoding="ascii").splitlines()[0])
            except (OSError, ValueError, IndexError):
                owner = -1
            if not _process_alive(owner):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"family run is already active: {path.parent}")
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


class FamilyContinuationWorker:
    def __init__(
        self,
        family: FamilyDefinition,
        anchor_candidate: CandidateRecord,
        anchor_result: EvaluationResult,
        run_directory: str | Path,
        executor: FamilyTaskExecutor,
        *,
        backend: LocalWorkerBackend | None = None,
        run_control: RunControl | None = None,
        observer: FamilyObserver | None = None,
        ingester: Callable[[FamilyRunStore], Any] | None = None,
        source_manifest_value: Mapping[str, Any] | None = None,
    ):
        raw_anchor = anchor_result.raw() if hasattr(anchor_result, "raw") else anchor_result
        if raw_anchor.status is not EvaluationStatus.FEASIBLE:
            raise ValueError("family continuation requires a feasible anchor result")
        if raw_anchor.evaluation_key != family.anchor_evaluation_key:
            raise ValueError("anchor result evaluation key does not match the family")
        if anchor_candidate.candidate_id != family.anchor_candidate_id:
            raise ValueError("anchor candidate does not match the family")
        self.family = family
        self.anchor_candidate = anchor_candidate
        self.anchor_result = raw_anchor
        self.executor = executor
        self.backend = backend or LocalWorkerBackend()
        self.run_control = run_control
        self.observer = observer or NullFamilyObserver()
        self.ingester = ingester
        self.cancel_event = threading.Event()
        self.store = FamilyRunStore(run_directory)
        self.planner = AxisContinuationPlanner(family)
        self.multidimensional_planner = MultidimensionalEpochPlanner(
            family, MultidimensionalPlannerConfig.from_family(family)
        )
        repository = Path(__file__).resolve().parents[2]
        self.source_manifest = dict(source_manifest_value or source_manifest(repository))
        self._initialize()

    def _initialize(self) -> None:
        anchor_sample = FamilySample(
            self.family.family_id,
            self.family.architecture_id,
            self.family.comparison_context,
            self.family.anchor_parameters,
            self.family.comparison_context.fidelity,
            continuation_epoch=0,
            classification=SampleClassification.FEASIBLE_FOUND,
            confidence=1.0,
            best_evaluation_key=self.anchor_result.evaluation_key,
            metrics=self.anchor_result.metrics,
        )
        anchor_attempt = FamilyAttempt(
            anchor_sample.sample_key,
            self.anchor_result.evaluation_key,
            self.anchor_result.candidate_id,
            0,
            None,
            self.anchor_result.fidelity,
            dict(self.anchor_result.provenance.get("budget", {})),
            self.anchor_result.status,
            self.anchor_result.solver_violation,
            self.anchor_result.runtime_seconds,
            dict(self.anchor_result.artifacts),
        )
        identity_payload = {
            "schema_version": FAMILY_RUN_CONFIG_SCHEMA,
            "family": self.family.to_dict(),
            "anchor_candidate": candidate_to_dict(self.anchor_candidate),
            "anchor_result": result_to_dict(self.anchor_result),
            "planner": self.planner.config.to_dict(),
            "multidimensional_planner": self.multidimensional_planner.config.to_dict(),
            "executor": dict(self.executor.context_identity()),
            "source_content_hash": self.source_manifest.get("content_hash"),
        }
        identity = content_hash(identity_payload, prefix="emtg-family-run-config-v1")
        resolved = {
            **identity_payload,
            "configuration_identity": identity,
            "source_manifest": self.source_manifest,
        }
        resolved_path = self.store.run_directory / "resolved-family.json"
        if resolved_path.is_file():
            current = json.loads(resolved_path.read_text(encoding="utf-8"))
            if current.get("configuration_identity") != identity:
                raise ValueError("resolved family configuration changed; choose a fresh run directory")
        else:
            atomic_write_json(resolved_path, resolved)
        if self.store.initialized:
            if self.store.get_metadata("configuration_identity") != identity:
                raise ValueError("family database configuration identity does not match")
            stored_source = self.store.get_metadata("source_manifest", {})
            if stored_source.get("content_hash") != self.source_manifest.get("content_hash"):
                raise ValueError("family run source content changed; choose a fresh run directory")
        else:
            self.store.initialize(
                resolved_document=resolved,
                configuration_identity=identity,
                source_manifest=self.source_manifest,
                anchor_sample=anchor_sample,
                anchor_attempt=anchor_attempt,
                anchor_candidate=self.anchor_candidate,
                anchor_result=self.anchor_result,
                chains=self.planner.initial_chains(anchor_sample.sample_key),
            )
            self._checkpoint("ready", None)
            self._ingest()
        self.multidimensional_planner.initialize(self.store, anchor_sample.sample_key)

    def cancel(self) -> None:
        self.cancel_event.set()

    def _samples_by_key(self) -> dict[str, FamilySample]:
        return {sample.sample_key: sample for sample in self.store.samples()}

    def _progress(self, status: str, reason: str | None) -> FamilyProgress:
        chains = self.store.chains()
        states = Counter(chain.state for chain in chains)
        samples = self._samples_by_key()
        multidimensional = None
        if self.multidimensional_planner.enabled:
            slice_rows = self.store.multidimensional_slices()
            ray_states = Counter(ray.state for ray in self.store.rays())
            multidimensional = {
                "preset": self.multidimensional_planner.config.preset.value,
                "slices": {
                    str(row["slice_key"]): {
                        "state": row["state"],
                        "new_coordinate_count": int(row["new_coordinate_count"]),
                        "promotion_count": int(row["promotion_count"]),
                    }
                    for row in slice_rows
                },
                "ray_states": dict(sorted(ray_states.items())),
                "sample_set_revision": self.store.sample_set_revision,
                "connectivity_revision": self.store.connectivity_revision,
                "boundary_products": [
                    {
                        "product_key": row["product_key"],
                        "product_revision": int(row["product_revision"]),
                        "content_hash": row["content_hash"],
                    }
                    for row in self.store.latest_boundary_products()
                ],
            }
        return FamilyProgress(
            status,
            reason,
            self.store.open_epoch(),
            self.store.last_closed_epoch(),
            self.backend.max_workers,
            len(chains),
            sum(chain.terminal for chain in chains),
            dict(sorted(states.items())),
            self.store.counts(),
            self.planner.range_summary(chains, samples),
            multidimensional,
        )

    def _checkpoint(self, status: str, reason: str | None) -> FamilyProgress:
        progress = self._progress(status, reason)
        self.store.checkpoint({
            "family_id": self.family.family_id,
            **progress.to_dict(),
        })
        self.observer.on_progress(progress)
        return progress

    def _ingest(self) -> None:
        if self.ingester is None:
            return
        try:
            self.ingester(self.store)
        except Exception as error:
            document = {"family_id": self.family.family_id, "error": str(error)}
            self.store.set_ingest_state(
                "run", "latest", document, status="failed", error=str(error)
            )

    def _continuation_seed(self, sample_key: str) -> ContinuationSeed:
        sample = self.store.sample(sample_key)
        result = self.store.best_result(sample_key)
        return ContinuationSeed.from_result(sample, result)

    @staticmethod
    def _prepared_document(prepared: Any) -> Mapping[str, Any] | None:
        if prepared is None:
            return None
        if hasattr(prepared, "to_dict"):
            return prepared.to_dict()
        if isinstance(prepared, Mapping):
            return dict(prepared)
        return {"repr": repr(prepared)}

    def _execute_task(self, task: StoredTask, prepared: Any) -> SampleEvaluationOutcome:
        prior_rows = self.store.attempts(task.sample_key)
        if prior_rows:
            task = replace(
                task,
                task={
                    **dict(task.task),
                    "previous_attempts": [
                        row["attempt"].to_dict() for row in prior_rows
                    ],
                    "previous_results": [
                        result_to_dict(row["result"]) for row in prior_rows
                    ],
                    "completed_rescue_stage_ordinals": [
                        (
                            row["attempt"].ordinal
                            if row["attempt"].rescue_stage_ordinal is None
                            else row["attempt"].rescue_stage_ordinal
                        )
                        for row in prior_rows
                    ],
                },
            )
        seed_keys = tuple(task.task.get("feasible_seed_keys", ()))
        feasible_seeds = tuple(self._continuation_seed(key) for key in seed_keys)

        def attempt_observer(
            request: EvaluationRequest,
            attempt: FamilyAttempt,
            result: EvaluationResult,
            cache_hit: bool,
        ) -> None:
            self.store.record_attempt(
                sample_key=task.sample_key,
                attempt=attempt,
                candidate=request.candidate,
                result=result,
                request=QueueRequest.from_evaluation_request(request).to_dict(),
                cache_hit=cache_hit,
            )
            self.observer.on_attempt(task.sample_key, attempt, cache_hit)

        return self.executor.evaluate(
            task,
            prepared,
            feasible_seeds,
            cancel_event=self.cancel_event,
            attempt_observer=attempt_observer,
        )

    def _plan_next_epoch(self) -> int | None:
        chains = self.store.chains()
        epoch = self.store.last_closed_epoch() + 1
        if all(chain.terminal for chain in chains):
            return self.multidimensional_planner.plan_epoch(self.store, epoch)
        samples = self._samples_by_key()
        feasible = self.store.completed_feasible_sample_keys(before_epoch=epoch)
        tasks: list[StoredTask] = []
        edges: list[ContinuationEdge] = []
        for chain in chains:
            planned = self.planner.plan_chain(
                chain, epoch=epoch, samples=samples, feasible_seed_keys=feasible
            )
            if planned is not None:
                task, edge = planned
                tasks.append(task)
                edges.append(edge)
        if not tasks:
            raise RuntimeError("active family chains produced no work")
        self.store.plan_epoch(epoch, tasks, edges)
        self._checkpoint("evaluating", "epoch_planned")
        return epoch

    def _close_epoch(self, epoch: int) -> None:
        if self.store.multidimensional_epoch(epoch):
            refinement_ids = {
                str(task.task["refinement_id"])
                for task in self.store.multidimensional_tasks_for_epoch(
                    epoch, states=("completed",)
                )
                if task.task.get("refinement_id")
            }
            self.multidimensional_planner.close_epoch(self.store, epoch)
            if len(refinement_ids) > 1:
                raise RuntimeError("one family epoch cannot contain multiple refinements")
            if refinement_ids:
                refinement_id = next(iter(refinement_ids))
                self.store.finish_refinement(
                    refinement_id,
                    "completed",
                    result={
                        "epoch": epoch,
                        "sample_revision": self.store.sample_set_revision,
                        "connectivity_revision": self.store.connectivity_revision,
                    },
                )
            self._checkpoint("evaluating", "multidimensional_epoch_closed")
            self._ingest()
            return
        samples = self._samples_by_key()
        chains = {chain.chain_id: chain for chain in self.store.chains()}
        tasks = self.store.tasks_for_epoch(epoch, states=("completed",))
        for task in sorted(tasks, key=lambda item: (item.chain_sequence, item.sample_key)):
            chain = chains[task.chain_id]
            updated = self.planner.apply_outcome(chain, task, samples[task.sample_key])
            chains[task.chain_id] = self.planner.finalize_bracket_state(updated, samples)
        self.store.close_epoch(epoch, tuple(sorted(chains.values(), key=lambda item: item.rank)))
        self._checkpoint("evaluating", "epoch_closed")
        self._ingest()

    def _all_complete(self) -> bool:
        return (
            all(chain.terminal for chain in self.store.chains())
            and self.multidimensional_planner.complete(self.store)
        )

    def _outcome(
        self, complete: bool, new_samples: int, reason: str | None
    ) -> FamilyRunOutcome:
        chains = self.store.chains()
        return FamilyRunOutcome(
            complete,
            self.family.family_id,
            self.store.open_epoch(),
            self.store.last_closed_epoch(),
            new_samples,
            sum(chain.terminal for chain in chains),
            len(chains),
            str(self.store.checkpoint_path),
            reason,
        )

    def run(self, *, max_new_samples: int | None = None) -> FamilyRunOutcome:
        if max_new_samples is not None and max_new_samples < 0:
            raise ValueError("max_new_samples cannot be negative")
        with _family_run_lock(self.store.run_directory / "family.lock"):
            self.store.verify_integrity()
            self.store.reset_running()
            checkpoint = self.store.load_checkpoint() or {}
            # A completed base run may later receive a durable refinement epoch.
            # The open epoch wins over the checkpoint mirror so refinements reuse
            # the same persistent family run and worker contract.
            if checkpoint.get("status") == "complete" and self.store.open_epoch() is None:
                return self._outcome(True, 0, checkpoint.get("reason"))
            new_samples = 0
            try:
                while True:
                    directive = self.run_control.snapshot() if self.run_control else None
                    if directive is not None and directive.core_limit is not None:
                        if directive.core_limit < 1:
                            raise ValueError("family run-control core limit must be positive")
                        self.backend.max_workers = int(directive.core_limit)
                    if self.cancel_event.is_set() or (directive is not None and directive.cancel):
                        self.cancel_event.set()
                        self.store.reset_running()
                        self._checkpoint("cancelled", "user_cancel")
                        self._ingest()
                        return self._outcome(False, new_samples, "user_cancel")
                    if directive is not None and directive.pause:
                        self.store.reset_running()
                        self._checkpoint("paused", "user_pause")
                        self._ingest()
                        return self._outcome(False, new_samples, "user_pause")

                    epoch = self.store.open_epoch()
                    if epoch is not None and self.store.epoch_complete(epoch):
                        # Closing a fully durable epoch performs no new solve and
                        # is therefore safe even when the caller's solve budget
                        # is exhausted. This is important after a soft pause.
                        self._close_epoch(epoch)
                        continue
                    if epoch is None and self._all_complete():
                        self._checkpoint("complete", "all_chains_terminal")
                        self._ingest()
                        return self._outcome(
                            True, new_samples, "all_chains_terminal"
                        )
                    if max_new_samples is not None and new_samples >= max_new_samples:
                        self.store.reset_running()
                        self._checkpoint("yielded", "sample_budget")
                        self._ingest()
                        return self._outcome(False, new_samples, "sample_budget")

                    if epoch is None:
                        epoch = self._plan_next_epoch()
                        if epoch is None:
                            if self._all_complete():
                                self._checkpoint("complete", "all_work_terminal")
                                self._ingest()
                                return self._outcome(True, new_samples, "all_work_terminal")
                            self._checkpoint("failed", "multidimensional_planner_stalled")
                            self._ingest()
                            raise RuntimeError(
                                "multidimensional planner has unfinished state but produced no work"
                            )

                    multidimensional_epoch = self.store.multidimensional_epoch(epoch)
                    pending = (
                        self.store.multidimensional_tasks_for_epoch(epoch, states=("planned",))
                        if multidimensional_epoch
                        else self.store.tasks_for_epoch(epoch, states=("planned",))
                    )
                    if not pending:
                        if self.store.epoch_complete(epoch):
                            self._close_epoch(epoch)
                            continue
                        raise RuntimeError(f"epoch {epoch} has no runnable tasks but is incomplete")
                    remaining = (
                        len(pending) if max_new_samples is None
                        else max_new_samples - new_samples
                    )
                    batch_size = min(self.backend.max_workers, len(pending), remaining)
                    if batch_size <= 0:
                        continue
                    batch = list(pending[:batch_size])
                    prepared_items: list[tuple[StoredTask, Any]] = []
                    for task in batch:
                        prepared = self.executor.prepare(task)
                        if multidimensional_epoch:
                            self.store.mark_multidimensional_running(
                                task, self._prepared_document(prepared)
                            )
                        else:
                            self.store.mark_running(
                                task.sample_key, self._prepared_document(prepared)
                            )
                        prepared_items.append((task, prepared))

                    for index, outcome in self.backend.map_stream(
                        prepared_items,
                        lambda item: self._execute_task(item[0], item[1]),
                    ):
                        task = prepared_items[index][0]
                        cancelled = self.cancel_event.is_set() or any(
                            result.status is EvaluationStatus.CANCELLED
                            for result in outcome.results
                        )
                        if cancelled:
                            continue
                        if multidimensional_epoch:
                            creates_sample = bool(task.task.get("creates_sample", True))
                            edge = (
                                self.multidimensional_planner.finalize_edge(
                                    self.store.edge_for_child(task.sample_key), outcome.sample
                                )
                                if creates_sample else None
                            )
                            self.store.complete_multidimensional_task(task, outcome.sample, edge)
                        else:
                            edge = self.planner.finalize_edge(
                                self.store.edge_for_child(task.sample_key), outcome.sample
                            )
                            self.store.complete_sample(outcome.sample, edge)
                        self.observer.on_sample(outcome.sample)
                        new_samples += 1
                        self._checkpoint("evaluating", "sample_completed")
                        self._ingest()

                    if self.cancel_event.is_set():
                        self.store.reset_running()
                        self._checkpoint("cancelled", "user_cancel")
                        self._ingest()
                        return self._outcome(False, new_samples, "user_cancel")
                    directive = self.run_control.snapshot() if self.run_control else None
                    if directive is not None and directive.pause:
                        self.store.reset_running()
                        self._checkpoint("paused", "user_pause")
                        self._ingest()
                        return self._outcome(False, new_samples, "user_pause")
                    if self.store.epoch_complete(epoch):
                        self._close_epoch(epoch)
            except Exception as error:
                self.store.reset_running()
                self._checkpoint("failed", str(error))
                self._ingest()
                raise
