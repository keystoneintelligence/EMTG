"""Deterministic multidimensional planning products for feasibility families.

This module deliberately contains no solver or viewer dependencies.  It owns the
versioned sampling sequences, pair-cell geometry, branch evidence, coverage
accounting, and conservative boundary products used by the family worker.
Evaluated :class:`FamilySample` objects remain the only source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, localcontext
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .atlas import (
    AxisDefinition,
    BranchDefinition,
    FamilyDefinition,
    FamilySample,
    ParameterTransform,
    ParameterVector,
    SampleClassification,
    decimal_value,
)
from .canonical import canonical_json, content_hash


MULTIDIMENSIONAL_PLANNER_SCHEMA = 1
BOUNDARY_PRODUCT_SCHEMA = 1
PREDICTION_SCHEMA = 1


class SearchPreset(str, Enum):
    QUICK = "quick"
    BALANCED = "balanced"
    DEEP = "deep"


@dataclass(frozen=True)
class MultidimensionalPlannerConfig:
    """Resolved, identity-bearing Chunk 5 effort configuration."""

    preset: SearchPreset = SearchPreset.QUICK
    selected_pairs: tuple[tuple[str, str], ...] = ()
    epoch_sample_limit: int = 0
    pair_sample_budget: int = 0
    pair_global_probe_count: int = 0
    pair_confirmation_budget: int = 0
    homogeneous_diagonal_target: Decimal = Decimal(1)
    ray_sample_limit: int = 16
    decision_distance_threshold: Decimal = Decimal("0.50")
    trajectory_distance_threshold: Decimal = Decimal("0.25")
    prediction_neighbors: int = 8
    schema_version: int = MULTIDIMENSIONAL_PLANNER_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "preset", SearchPreset(self.preset))
        pairs: list[tuple[str, str]] = []
        for raw in self.selected_pairs:
            if len(raw) != 2:
                raise ValueError("each multidimensional selected pair must contain two axes")
            first, second = sorted((str(raw[0]), str(raw[1])))
            if not first or first == second:
                raise ValueError("multidimensional pairs require two distinct nonempty axes")
            pairs.append((first, second))
        if len(set(pairs)) != len(pairs):
            raise ValueError("multidimensional selected pairs contain duplicates")
        object.__setattr__(self, "selected_pairs", tuple(sorted(pairs)))
        for name in (
            "epoch_sample_limit", "pair_sample_budget", "pair_global_probe_count",
            "pair_confirmation_budget", "ray_sample_limit", "prediction_neighbors",
        ):
            if int(getattr(self, name)) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.preset is not SearchPreset.QUICK and self.epoch_sample_limit < 1:
            raise ValueError("multidimensional epochs require a positive sample limit")
        if self.ray_sample_limit < 1 or self.prediction_neighbors < 1:
            raise ValueError("ray and prediction limits must be positive")
        for name in (
            "homogeneous_diagonal_target", "decision_distance_threshold",
            "trajectory_distance_threshold",
        ):
            value = decimal_value(getattr(self, name), where=name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.schema_version != MULTIDIMENSIONAL_PLANNER_SCHEMA:
            raise ValueError("unsupported multidimensional planner schema")

    @classmethod
    def from_family(cls, family: FamilyDefinition) -> "MultidimensionalPlannerConfig":
        raw = family.planner_configuration.get("multidimensional", {})
        if not isinstance(raw, Mapping):
            raise ValueError("planner_configuration.multidimensional must be an object")
        allowed = {
            "schema_version", "preset", "selected_pairs", "epoch_sample_limit",
            "pair_sample_budget", "pair_global_probe_count", "pair_confirmation_budget",
            "homogeneous_diagonal_target", "ray_sample_limit",
            "decision_distance_threshold", "trajectory_distance_threshold",
            "prediction_neighbors",
        }
        unknown = sorted(set(raw) - allowed)
        if unknown:
            raise ValueError(
                "unknown multidimensional planner field(s): " + ", ".join(unknown)
            )
        preset = SearchPreset(str(raw.get("preset", "quick")))
        defaults: dict[SearchPreset, dict[str, Any]] = {
            SearchPreset.QUICK: {
                "epoch_sample_limit": 0,
                "pair_sample_budget": 0,
                "pair_global_probe_count": 0,
                "pair_confirmation_budget": 0,
                "homogeneous_diagonal_target": "1",
            },
            SearchPreset.BALANCED: {
                "epoch_sample_limit": 32,
                "pair_sample_budget": 128,
                "pair_global_probe_count": 16,
                "pair_confirmation_budget": 32,
                "homogeneous_diagonal_target": "0.125",
            },
            SearchPreset.DEEP: {
                "epoch_sample_limit": 64,
                "pair_sample_budget": 512,
                "pair_global_probe_count": 64,
                "pair_confirmation_budget": 128,
                "homogeneous_diagonal_target": "0.0625",
            },
        }
        axes = tuple(sorted(axis.parameter_key for axis in family.axes))
        requested_pairs = tuple(
            tuple(map(str, item)) for item in raw.get("selected_pairs", ())
        )
        if preset is SearchPreset.QUICK:
            if requested_pairs:
                raise ValueError("quick family search cannot configure 2D pairs")
            pairs: tuple[tuple[str, str], ...] = ()
        elif preset is SearchPreset.DEEP:
            if requested_pairs:
                raise ValueError("deep family search always uses all axis pairs")
            pairs = tuple(
                (axes[first], axes[second])
                for first in range(len(axes))
                for second in range(first + 1, len(axes))
            )
        elif len(axes) < 2:
            raise ValueError("balanced family search requires at least two axes")
        elif len(axes) == 2 and not requested_pairs:
            pairs = ((axes[0], axes[1]),)
        else:
            if not requested_pairs:
                raise ValueError(
                    "balanced family search with more than two axes requires selected_pairs"
                )
            pairs = requested_pairs
        known = set(axes)
        missing = sorted({key for pair in pairs for key in pair} - known)
        if missing:
            raise ValueError("multidimensional pair axes are not in the family: " + ", ".join(missing))
        selected = defaults[preset]
        return cls(
            preset,
            pairs,
            int(raw.get("epoch_sample_limit", selected["epoch_sample_limit"])),
            int(raw.get("pair_sample_budget", selected["pair_sample_budget"])),
            int(raw.get("pair_global_probe_count", selected["pair_global_probe_count"])),
            int(raw.get("pair_confirmation_budget", selected["pair_confirmation_budget"])),
            decimal_value(
                raw.get("homogeneous_diagonal_target", selected["homogeneous_diagonal_target"])
            ),
            int(raw.get("ray_sample_limit", 16)),
            decimal_value(raw.get("decision_distance_threshold", "0.50")),
            decimal_value(raw.get("trajectory_distance_threshold", "0.25")),
            int(raw.get("prediction_neighbors", 8)),
            int(raw.get("schema_version", MULTIDIMENSIONAL_PLANNER_SCHEMA)),
        )

    @property
    def enabled(self) -> bool:
        return self.preset is not SearchPreset.QUICK

    def high_dimensional_ray_count(self, dimension: int) -> int:
        if self.preset is not SearchPreset.DEEP or dimension < 3:
            return 0
        return 2 * min(32, max(8, 2 * dimension))

    def high_dimensional_global_probe_count(self, dimension: int) -> int:
        if self.preset is not SearchPreset.DEEP or dimension < 3:
            return 0
        return min(64, max(16, 4 * dimension))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "preset": self.preset.value,
            "selected_pairs": [list(item) for item in self.selected_pairs],
            "epoch_sample_limit": self.epoch_sample_limit,
            "pair_sample_budget": self.pair_sample_budget,
            "pair_global_probe_count": self.pair_global_probe_count,
            "pair_confirmation_budget": self.pair_confirmation_budget,
            "homogeneous_diagonal_target": str(self.homogeneous_diagonal_target),
            "ray_sample_limit": self.ray_sample_limit,
            "decision_distance_threshold": str(self.decision_distance_threshold),
            "trajectory_distance_threshold": str(self.trajectory_distance_threshold),
            "prediction_neighbors": self.prediction_neighbors,
        }


@dataclass(frozen=True)
class PairSliceDefinition:
    family_id: str
    x_axis_key: str
    y_axis_key: str
    fixed_parameters: ParameterVector

    def __post_init__(self) -> None:
        x_key, y_key = sorted((self.x_axis_key, self.y_axis_key))
        if not x_key or x_key == y_key:
            raise ValueError("pair slice requires two distinct axes")
        object.__setattr__(self, "x_axis_key", x_key)
        object.__setattr__(self, "y_axis_key", y_key)
        if x_key in self.fixed_parameters or y_key in self.fixed_parameters:
            raise ValueError("pair slice fixed parameters cannot contain either projected axis")

    @property
    def pair_key(self) -> str:
        return content_hash(
            {
                "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
                "family_id": self.family_id,
                "axes": [self.x_axis_key, self.y_axis_key],
            },
            prefix="emtg-family-pair-v1",
        )

    @property
    def slice_key(self) -> str:
        return content_hash(
            {
                "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
                "pair_key": self.pair_key,
                "fixed_parameters": self.fixed_parameters.to_dict(),
            },
            prefix="emtg-family-pair-slice-v1",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
            "pair_key": self.pair_key,
            "slice_key": self.slice_key,
            "family_id": self.family_id,
            "x_axis_key": self.x_axis_key,
            "y_axis_key": self.y_axis_key,
            "fixed_parameters": self.fixed_parameters.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PairSliceDefinition":
        allowed = {
            "schema_version", "pair_key", "slice_key", "family_id",
            "x_axis_key", "y_axis_key", "fixed_parameters",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError("unknown pair-slice field(s): " + ", ".join(unknown))
        if int(value.get("schema_version", -1)) != MULTIDIMENSIONAL_PLANNER_SCHEMA:
            raise ValueError("unsupported pair-slice schema")
        result = cls(
            str(value["family_id"]), str(value["x_axis_key"]), str(value["y_axis_key"]),
            ParameterVector.from_dict(value["fixed_parameters"]),
        )
        if value.get("pair_key", result.pair_key) != result.pair_key:
            raise ValueError("pair_key does not match pair slice")
        if value.get("slice_key", result.slice_key) != result.slice_key:
            raise ValueError("slice_key does not match pair slice")
        return result


def first_primes(count: int) -> tuple[int, ...]:
    if count < 0:
        raise ValueError("prime count cannot be negative")
    output: list[int] = []
    candidate = 2
    while len(output) < count:
        if all(candidate % prime for prime in output if prime * prime <= candidate):
            output.append(candidate)
        candidate += 1
    return tuple(output)


def radical_inverse(index: int, base: int) -> Decimal:
    if index < 1 or base < 2:
        raise ValueError("Halton indices start at one and bases must be at least two")
    with localcontext() as context:
        context.prec = 60
        value = Decimal(0)
        factor = Decimal(1) / Decimal(base)
        remaining = index
        while remaining:
            remaining, digit = divmod(remaining, base)
            value += factor * digit
            factor /= base
        return +value


def halton_point(index: int, dimension: int) -> tuple[Decimal, ...]:
    return tuple(
        radical_inverse(index, base) for base in first_primes(dimension)
    )


def halton_directions(count: int, dimension: int) -> tuple[tuple[Decimal, ...], ...]:
    """Return a nested sequence of centered Halton directions and antipodes."""
    if count < 0 or count % 2:
        raise ValueError("direction count must be nonnegative and even")
    if dimension < 1:
        raise ValueError("directions require at least one dimension")
    output: list[tuple[Decimal, ...]] = []
    index = 1
    with localcontext() as context:
        context.prec = 60
        while len(output) < count:
            centered = tuple(Decimal(2) * item - Decimal(1) for item in halton_point(index, dimension))
            norm_squared = sum(item * item for item in centered)
            index += 1
            if norm_squared == 0:
                continue
            norm = norm_squared.sqrt()
            direction = tuple(+(item / norm) for item in centered)
            output.extend((direction, tuple(-item for item in direction)))
    return tuple(output[:count])


@dataclass(frozen=True)
class PairGrid:
    axis: AxisDefinition
    size: int
    anchor_index: int

    @classmethod
    def from_family(cls, family: FamilyDefinition, axis_key: str) -> "PairGrid":
        axis = next((item for item in family.axes if item.parameter_key == axis_key), None)
        if axis is None:
            raise KeyError(axis_key)
        raw_size = (axis.upper - axis.lower) / axis.resolution
        if raw_size != raw_size.to_integral_value():
            raise ValueError(f"axis {axis_key} width is not an integral resolution multiple")
        raw_anchor = (decimal_value(family.anchor_parameters[axis_key]) - axis.lower) / axis.resolution
        if raw_anchor != raw_anchor.to_integral_value():
            raise ValueError(f"anchor value for {axis_key} is off grid")
        return cls(axis, int(raw_size), int(raw_anchor))

    def value(self, index: int) -> Decimal:
        if not 0 <= index <= self.size:
            raise ValueError(f"grid index {index} is outside {self.axis.parameter_key}")
        return self.axis.lower + self.axis.resolution * index

    def index(self, value: Any) -> int:
        raw = (decimal_value(value) - self.axis.lower) / self.axis.resolution
        if raw != raw.to_integral_value() or not 0 <= int(raw) <= self.size:
            raise ValueError(f"value is off the {self.axis.parameter_key} grid")
        return int(raw)

    def transformed(self, index: int) -> Decimal:
        value = self.value(index)
        if self.axis.transform is ParameterTransform.LINEAR:
            return value
        if value <= 0:
            raise ValueError(f"log axis {self.axis.parameter_key} contains a nonpositive value")
        with localcontext() as context:
            context.prec = 60
            return value.log10()

    def normalized(self, index: int) -> Decimal:
        lower = self.transformed(0)
        upper = self.transformed(self.size)
        if upper == lower:
            return Decimal(0)
        return (self.transformed(index) - lower) / (upper - lower)

    def snap_normalized(self, value: Decimal) -> int:
        clipped = max(Decimal(0), min(Decimal(1), decimal_value(value)))
        lower_t = self.transformed(0)
        upper_t = self.transformed(self.size)
        target_t = lower_t + clipped * (upper_t - lower_t)
        best_index = 0
        best_distance: Decimal | None = None
        for index in range(self.size + 1):
            distance = abs(self.transformed(index) - target_t)
            if best_distance is None or distance < best_distance:
                best_index, best_distance = index, distance
        return best_index


class CellState(str, Enum):
    UNRESOLVED = "unresolved"
    MIXED = "mixed"
    HOMOGENEOUS_EVIDENCE = "homogeneous_evidence"
    UNKNOWN_BLOCKED = "unknown_blocked"
    TERMINAL_BOUNDARY = "terminal_boundary"


@dataclass(frozen=True)
class QuadtreeCell:
    slice_key: str
    x_lower: int
    x_upper: int
    y_lower: int
    y_upper: int
    level: int = 0
    parent_cell_id: str | None = None
    state: CellState = CellState.UNRESOLVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", CellState(self.state))
        if self.x_lower >= self.x_upper or self.y_lower >= self.y_upper:
            raise ValueError("quadtree cells must cover nonzero area")
        if self.level < 0:
            raise ValueError("quadtree level cannot be negative")

    @property
    def cell_id(self) -> str:
        return content_hash(
            {
                "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
                "slice_key": self.slice_key,
                "bounds": [self.x_lower, self.x_upper, self.y_lower, self.y_upper],
            },
            prefix="emtg-family-quadtree-cell-v1",
        )

    @property
    def terminal(self) -> bool:
        return self.x_upper - self.x_lower <= 1 and self.y_upper - self.y_lower <= 1

    def stencil(self) -> tuple[tuple[int, int], ...]:
        x_mid = (self.x_lower + self.x_upper) // 2
        y_mid = (self.y_lower + self.y_upper) // 2
        return tuple(sorted({
            (x, y)
            for x in (self.x_lower, x_mid, self.x_upper)
            for y in (self.y_lower, y_mid, self.y_upper)
        }))

    def split(self) -> tuple["QuadtreeCell", ...]:
        if self.terminal:
            return ()
        x_mid = (self.x_lower + self.x_upper) // 2
        y_mid = (self.y_lower + self.y_upper) // 2
        x_ranges = (
            ((self.x_lower, self.x_upper),)
            if self.x_upper - self.x_lower <= 1
            else ((self.x_lower, x_mid), (x_mid, self.x_upper))
        )
        y_ranges = (
            ((self.y_lower, self.y_upper),)
            if self.y_upper - self.y_lower <= 1
            else ((self.y_lower, y_mid), (y_mid, self.y_upper))
        )
        return tuple(
            QuadtreeCell(
                self.slice_key, x_lower, x_upper, y_lower, y_upper,
                self.level + 1, self.cell_id,
            )
            for x_lower, x_upper in x_ranges
            for y_lower, y_upper in y_ranges
        )

    def normalized_diagonal(self, x_grid: PairGrid, y_grid: PairGrid) -> Decimal:
        x_span = x_grid.normalized(self.x_upper) - x_grid.normalized(self.x_lower)
        y_span = y_grid.normalized(self.y_upper) - y_grid.normalized(self.y_lower)
        with localcontext() as context:
            context.prec = 60
            return (x_span * x_span + y_span * y_span).sqrt()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
            "cell_id": self.cell_id,
            "slice_key": self.slice_key,
            "x_lower": self.x_lower,
            "x_upper": self.x_upper,
            "y_lower": self.y_lower,
            "y_upper": self.y_upper,
            "level": self.level,
            "parent_cell_id": self.parent_cell_id,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "QuadtreeCell":
        result = cls(
            str(value["slice_key"]), int(value["x_lower"]), int(value["x_upper"]),
            int(value["y_lower"]), int(value["y_upper"]), int(value.get("level", 0)),
            value.get("parent_cell_id"), CellState(value.get("state", "unresolved")),
        )
        if value.get("cell_id", result.cell_id) != result.cell_id:
            raise ValueError("cell_id does not match quadtree cell")
        return result


@dataclass(frozen=True)
class RayState:
    family_id: str
    ordinal: int
    direction: tuple[Decimal, ...]
    state: str
    current_feasible_sample_key: str
    current_radius: Decimal = Decimal(0)
    previous_feasible_sample_key: str | None = None
    previous_radius: Decimal | None = None
    not_found_sample_key: str | None = None
    not_found_radius: Decimal | None = None
    successful_scouts: int = 0
    refinement_count: int = 0
    evaluated_target_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "direction", tuple(decimal_value(item) for item in self.direction))
        object.__setattr__(self, "current_radius", decimal_value(self.current_radius))
        if self.previous_radius is not None:
            object.__setattr__(self, "previous_radius", decimal_value(self.previous_radius))
        if self.not_found_radius is not None:
            object.__setattr__(self, "not_found_radius", decimal_value(self.not_found_radius))
        if self.ordinal < 0 or not self.direction:
            raise ValueError("ray ordinal and direction are invalid")
        if self.state not in {
            "scouting", "refining", "bound_feasible", "bracketed",
            "blocked_unknown", "budget_exhausted",
        }:
            raise ValueError(f"unsupported ray state {self.state!r}")

    @property
    def ray_id(self) -> str:
        return content_hash(
            {
                "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
                "family_id": self.family_id,
                "ordinal": self.ordinal,
                "direction": [str(item) for item in self.direction],
            },
            prefix="emtg-family-high-dimensional-ray-v1",
        )

    @property
    def terminal(self) -> bool:
        return self.state in {
            "bound_feasible", "bracketed", "blocked_unknown", "budget_exhausted",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
            "ray_id": self.ray_id,
            "family_id": self.family_id,
            "ordinal": self.ordinal,
            "direction": [str(item) for item in self.direction],
            "state": self.state,
            "current_feasible_sample_key": self.current_feasible_sample_key,
            "current_radius": str(self.current_radius),
            "previous_feasible_sample_key": self.previous_feasible_sample_key,
            "previous_radius": None if self.previous_radius is None else str(self.previous_radius),
            "not_found_sample_key": self.not_found_sample_key,
            "not_found_radius": None if self.not_found_radius is None else str(self.not_found_radius),
            "successful_scouts": self.successful_scouts,
            "refinement_count": self.refinement_count,
            "evaluated_target_count": self.evaluated_target_count,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RayState":
        result = cls(
            str(value["family_id"]), int(value["ordinal"]),
            tuple(decimal_value(item) for item in value["direction"]), str(value["state"]),
            str(value["current_feasible_sample_key"]), value.get("current_radius", "0"),
            value.get("previous_feasible_sample_key"), value.get("previous_radius"),
            value.get("not_found_sample_key"), value.get("not_found_radius"),
            int(value.get("successful_scouts", 0)), int(value.get("refinement_count", 0)),
            int(value.get("evaluated_target_count", 0)),
        )
        if value.get("ray_id", result.ray_id) != result.ray_id:
            raise ValueError("ray_id does not match ray state")
        return result


def root_cells(slice_key: str, x_grid: PairGrid, y_grid: PairGrid) -> tuple[QuadtreeCell, ...]:
    x_ranges = {
        pair for pair in ((0, x_grid.anchor_index), (x_grid.anchor_index, x_grid.size))
        if pair[0] < pair[1]
    }
    y_ranges = {
        pair for pair in ((0, y_grid.anchor_index), (y_grid.anchor_index, y_grid.size))
        if pair[0] < pair[1]
    }
    return tuple(
        QuadtreeCell(slice_key, x_lower, x_upper, y_lower, y_upper)
        for x_lower, x_upper in sorted(x_ranges)
        for y_lower, y_upper in sorted(y_ranges)
    )


def classify_cell(
    cell: QuadtreeCell,
    samples: Mapping[tuple[int, int], FamilySample],
    *,
    promotion_available: bool = True,
) -> CellState:
    present = [samples[point] for point in cell.stencil() if point in samples]
    feasible = [
        item for item in present
        if item.classification is SampleClassification.FEASIBLE_FOUND
    ]
    negative = [
        item for item in present
        if item.classification is SampleClassification.CONFIRMED_NOT_FOUND
    ]
    unknown = [
        item for item in present if item.classification is SampleClassification.UNKNOWN
    ]
    branches = {item.branch_id for item in feasible if item.branch_id is not None}
    mixed = bool(feasible and negative) or len(branches) > 1
    if mixed:
        return CellState.TERMINAL_BOUNDARY if cell.terminal else CellState.MIXED
    if unknown and not promotion_available:
        return CellState.UNKNOWN_BLOCKED
    conclusive = feasible + negative
    if conclusive:
        return CellState.HOMOGENEOUS_EVIDENCE
    return CellState.UNRESOLVED


def decision_vector_distance(parent: FamilySample, child: FamilySample) -> float | None:
    p_desc = tuple(map(str, parent.metrics.get("xdescriptions", ())))
    c_desc = tuple(map(str, child.metrics.get("xdescriptions", ())))
    p_vec = tuple(float(value) for value in parent.metrics.get("decision_vector", ()))
    c_vec = tuple(float(value) for value in child.metrics.get("decision_vector", ()))
    if not p_desc or p_desc != c_desc or len(p_vec) != len(p_desc) or len(c_vec) != len(p_desc):
        return None
    lower = tuple(float(value) for value in parent.metrics.get("decision_vector_lower_bounds", ()))
    upper = tuple(float(value) for value in parent.metrics.get("decision_vector_upper_bounds", ()))
    normalized: list[float] = []
    for index, (first, second) in enumerate(zip(p_vec, c_vec)):
        if len(lower) == len(p_vec) and len(upper) == len(p_vec):
            width = upper[index] - lower[index]
        else:
            width = 0.0
        scale = width if math.isfinite(width) and width > 0 else max(1.0, abs(first), abs(second))
        normalized.append((second - first) / scale)
    return math.sqrt(sum(value * value for value in normalized) / len(normalized))


def _events(sample: FamilySample) -> tuple[Mapping[str, Any], ...]:
    raw = sample.metrics.get("mission_events", ())
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return ()
    return tuple(item for item in raw if isinstance(item, Mapping))


def trajectory_distance(parent: FamilySample, child: FamilySample) -> tuple[bool | None, float | None]:
    p_events, c_events = _events(parent), _events(child)
    if not p_events or not c_events:
        return None, None
    p_structure = tuple((str(item.get("event_type")), str(item.get("location"))) for item in p_events)
    c_structure = tuple((str(item.get("event_type")), str(item.get("location"))) for item in c_events)
    if p_structure != c_structure:
        return False, None
    p_start = float(p_events[0].get("julian_date_mjd") or 0.0)
    c_start = float(c_events[0].get("julian_date_mjd") or 0.0)
    p_end = float(p_events[-1].get("julian_date_mjd") or p_start)
    c_end = float(c_events[-1].get("julian_date_mjd") or c_start)
    duration = max(1.0, abs(p_end - p_start), abs(c_end - c_start))
    values: list[float] = []
    for first, second in zip(p_events, c_events):
        first_epoch = first.get("julian_date_mjd")
        second_epoch = second.get("julian_date_mjd")
        if first_epoch is not None and second_epoch is not None:
            values.append(
                ((float(second_epoch) - c_start) - (float(first_epoch) - p_start)) / duration
            )
        for key in ("position_km", "velocity_km_s"):
            first_vector, second_vector = first.get(key), second.get(key)
            if (
                isinstance(first_vector, Sequence) and isinstance(second_vector, Sequence)
                and len(first_vector) == len(second_vector) == 3
            ):
                first_values = tuple(float(item) for item in first_vector)
                second_values = tuple(float(item) for item in second_vector)
                scale = max(
                    1.0,
                    math.sqrt(sum(item * item for item in first_values)),
                    math.sqrt(sum(item * item for item in second_values)),
                )
                values.extend(
                    (right - left) / scale
                    for left, right in zip(first_values, second_values)
                )
        first_mass, second_mass = first.get("mass"), second.get("mass")
        if first_mass is not None and second_mass is not None:
            scale = max(1.0, abs(float(first_mass)), abs(float(second_mass)))
            values.append((float(second_mass) - float(first_mass)) / scale)
    if not values:
        return True, None
    return True, math.sqrt(sum(value * value for value in values) / len(values))


def branch_evidence(
    parent: FamilySample,
    child: FamilySample,
    *,
    decision_threshold: Decimal = Decimal("0.50"),
    trajectory_threshold: Decimal = Decimal("0.25"),
) -> dict[str, Any]:
    decision = decision_vector_distance(parent, child)
    structural_match, trajectory = trajectory_distance(parent, child)
    split = structural_match is False or (
        decision is not None and trajectory is not None
        and decision >= float(decision_threshold)
        and trajectory >= float(trajectory_threshold)
    )
    return {
        "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
        "decision_distance": decision,
        "trajectory_structural_match": structural_match,
        "trajectory_distance": trajectory,
        "decision_threshold": str(decision_threshold),
        "trajectory_threshold": str(trajectory_threshold),
        "branch_split": split,
        "evidence_complete": decision is not None and structural_match is not None,
    }


@dataclass(frozen=True)
class Prediction:
    sample_key: str | None
    parameter_key: str
    grid_index: tuple[int, int]
    feasible_probability: float
    source_sample_revision: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PREDICTION_SCHEMA,
            "sample_key": self.sample_key,
            "parameter_key": self.parameter_key,
            "grid_index": list(self.grid_index),
            "feasible_probability": self.feasible_probability,
            "source_sample_revision": self.source_sample_revision,
            "authoritative": False,
        }


def inverse_distance_prediction(
    target: tuple[Decimal, Decimal],
    samples: Sequence[tuple[tuple[Decimal, Decimal], FamilySample]],
    *,
    neighbors: int = 8,
) -> float | None:
    evidence: list[tuple[float, str, float]] = []
    for point, sample in samples:
        if sample.classification not in {
            SampleClassification.FEASIBLE_FOUND,
            SampleClassification.CONFIRMED_NOT_FOUND,
        }:
            continue
        distance = math.hypot(float(point[0] - target[0]), float(point[1] - target[1]))
        if distance == 0:
            return 1.0 if sample.classification is SampleClassification.FEASIBLE_FOUND else 0.0
        evidence.append((distance, sample.sample_key, 1.0 if sample.classification is SampleClassification.FEASIBLE_FOUND else 0.0))
    if not evidence:
        return None
    selected = sorted(evidence)[:neighbors]
    floor = min(item[0] for item in selected)
    weights = [1.0 / max(item[0], floor) ** 2 for item in selected]
    return sum(weight * item[2] for weight, item in zip(weights, selected)) / sum(weights)


@dataclass(frozen=True)
class CoverageSnapshot:
    product_key: str
    source_sample_revision: int
    source_connectivity_revision: int
    evaluated_nodes: int
    conclusive_nodes: int
    unknown_nodes: int
    total_grid_nodes: int
    total_unit_cells: int
    feasible_unit_cells: int
    not_found_unit_cells: int
    mixed_unit_cells: int
    unresolved_unit_cells: int
    largest_unresolved_diagonal: Decimal
    global_probes_completed: int
    global_probes_target: int
    promotions_completed: int
    promotion_budget: int
    ray_states: Mapping[str, int] = field(default_factory=dict)
    high_dimensional_fill_distance_max: float | None = None
    high_dimensional_fill_distance_rms: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "largest_unresolved_diagonal", decimal_value(self.largest_unresolved_diagonal))
        object.__setattr__(self, "ray_states", MappingProxyType(dict(sorted(self.ray_states.items()))))

    @property
    def unresolved_area_fraction(self) -> float:
        if self.total_unit_cells == 0:
            return 0.0
        return self.unresolved_unit_cells / self.total_unit_cells

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MULTIDIMENSIONAL_PLANNER_SCHEMA,
            "product_key": self.product_key,
            "source_sample_revision": self.source_sample_revision,
            "source_connectivity_revision": self.source_connectivity_revision,
            "evaluated_nodes": self.evaluated_nodes,
            "conclusive_nodes": self.conclusive_nodes,
            "unknown_nodes": self.unknown_nodes,
            "total_grid_nodes": self.total_grid_nodes,
            "total_unit_cells": self.total_unit_cells,
            "feasible_unit_cells": self.feasible_unit_cells,
            "not_found_unit_cells": self.not_found_unit_cells,
            "mixed_unit_cells": self.mixed_unit_cells,
            "unresolved_unit_cells": self.unresolved_unit_cells,
            "unresolved_area_fraction": self.unresolved_area_fraction,
            "largest_unresolved_diagonal": str(self.largest_unresolved_diagonal),
            "global_probes_completed": self.global_probes_completed,
            "global_probes_target": self.global_probes_target,
            "promotions_completed": self.promotions_completed,
            "promotion_budget": self.promotion_budget,
            "promotion_exhausted": (
                self.promotion_budget > 0
                and self.promotions_completed >= self.promotion_budget
            ),
            "ray_states": dict(self.ray_states),
            "high_dimensional_fill_distance_max": self.high_dimensional_fill_distance_max,
            "high_dimensional_fill_distance_rms": self.high_dimensional_fill_distance_rms,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CoverageSnapshot":
        if int(value.get("schema_version", -1)) != MULTIDIMENSIONAL_PLANNER_SCHEMA:
            raise ValueError("unsupported coverage-snapshot schema")
        return cls(
            str(value["product_key"]),
            int(value["source_sample_revision"]),
            int(value["source_connectivity_revision"]),
            int(value["evaluated_nodes"]),
            int(value["conclusive_nodes"]),
            int(value["unknown_nodes"]),
            int(value["total_grid_nodes"]),
            int(value["total_unit_cells"]),
            int(value["feasible_unit_cells"]),
            int(value["not_found_unit_cells"]),
            int(value["mixed_unit_cells"]),
            int(value["unresolved_unit_cells"]),
            decimal_value(value["largest_unresolved_diagonal"]),
            int(value["global_probes_completed"]),
            int(value["global_probes_target"]),
            int(value["promotions_completed"]),
            int(value["promotion_budget"]),
            {str(key): int(item) for key, item in value.get("ray_states", {}).items()},
            (
                None if value.get("high_dimensional_fill_distance_max") is None
                else float(value["high_dimensional_fill_distance_max"])
            ),
            (
                None if value.get("high_dimensional_fill_distance_rms") is None
                else float(value["high_dimensional_fill_distance_rms"])
            ),
        )


@dataclass(frozen=True)
class BoundaryProduct:
    product_key: str
    product_revision: int
    source_sample_revision: int
    source_connectivity_revision: int
    config_hash: str
    slice_definition: PairSliceDefinition
    exact_samples: tuple[Mapping[str, Any], ...]
    feasible_components: tuple[Mapping[str, Any], ...]
    not_found_tiles: tuple[tuple[int, int], ...]
    boundary_bands: tuple[tuple[int, int], ...]
    unknown_bands: tuple[tuple[int, int], ...]
    predictions: tuple[Mapping[str, Any], ...]
    coverage: CoverageSnapshot

    def __post_init__(self) -> None:
        if self.product_revision < 1:
            raise ValueError("boundary product revisions start at one")
        object.__setattr__(self, "exact_samples", tuple(dict(item) for item in self.exact_samples))
        object.__setattr__(self, "feasible_components", tuple(dict(item) for item in self.feasible_components))
        exact_points = {
            tuple(map(int, item["grid_index"])) for item in self.exact_samples
        }
        exact_keys = {
            str(item["sample_key"]) for item in self.exact_samples
            if item.get("sample_key") is not None
        }
        object.__setattr__(self, "predictions", tuple(
            dict(item) for item in self.predictions
            if tuple(map(int, item.get("grid_index", ()))) not in exact_points
            and (
                item.get("sample_key") is None
                or str(item["sample_key"]) not in exact_keys
            )
        ))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BOUNDARY_PRODUCT_SCHEMA,
            "product_key": self.product_key,
            "product_revision": self.product_revision,
            "source_sample_revision": self.source_sample_revision,
            "source_connectivity_revision": self.source_connectivity_revision,
            "config_hash": self.config_hash,
            "slice": self.slice_definition.to_dict(),
            "exact_samples": [dict(item) for item in self.exact_samples],
            "feasible_components": [dict(item) for item in self.feasible_components],
            "not_found_tiles": [list(item) for item in self.not_found_tiles],
            "boundary_bands": [list(item) for item in self.boundary_bands],
            "unknown_bands": [list(item) for item in self.unknown_bands],
            "predictions": [dict(item) for item in self.predictions],
            "coverage": self.coverage.to_dict(),
        }

    @property
    def content_hash(self) -> str:
        return content_hash(self.to_dict(), prefix="emtg-family-boundary-product-v1")

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BoundaryProduct":
        if int(value.get("schema_version", -1)) != BOUNDARY_PRODUCT_SCHEMA:
            raise ValueError("unsupported boundary-product schema")
        result = cls(
            str(value["product_key"]),
            int(value["product_revision"]),
            int(value["source_sample_revision"]),
            int(value["source_connectivity_revision"]),
            str(value["config_hash"]),
            PairSliceDefinition.from_dict(value["slice"]),
            tuple(dict(item) for item in value.get("exact_samples", ())),
            tuple(dict(item) for item in value.get("feasible_components", ())),
            tuple(tuple(map(int, item)) for item in value.get("not_found_tiles", ())),
            tuple(tuple(map(int, item)) for item in value.get("boundary_bands", ())),
            tuple(tuple(map(int, item)) for item in value.get("unknown_bands", ())),
            tuple(dict(item) for item in value.get("predictions", ())),
            CoverageSnapshot.from_dict(value["coverage"]),
        )
        if result.product_key != result.slice_definition.slice_key:
            raise ValueError("boundary product key does not match its slice")
        return result


def _component_rings(cells: set[tuple[int, int]]) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Trace oriented exposed edges of a four-connected unit-cell component."""
    edges: set[tuple[tuple[int, int], tuple[int, int]]] = set()
    for x, y in cells:
        directed = (
            ((x, y), (x + 1, y)),
            ((x + 1, y), (x + 1, y + 1)),
            ((x + 1, y + 1), (x, y + 1)),
            ((x, y + 1), (x, y)),
        )
        for edge in directed:
            reverse = (edge[1], edge[0])
            if reverse in edges:
                edges.remove(reverse)
            else:
                edges.add(edge)
    rings: list[tuple[tuple[int, int], ...]] = []
    while edges:
        first = min(edges)
        edges.remove(first)
        ring = [first[0], first[1]]
        while ring[-1] != ring[0]:
            candidates = sorted(edge for edge in edges if edge[0] == ring[-1])
            if not candidates:
                raise ValueError("feasible tile edges do not form closed rings")
            selected = candidates[0]
            edges.remove(selected)
            ring.append(selected[1])
        rings.append(tuple(ring))
    return tuple(sorted(rings, key=lambda item: (min(item), len(item), item)))


def _connected_cell_components(cells: set[tuple[int, int]]) -> tuple[set[tuple[int, int]], ...]:
    remaining = set(cells)
    output: list[set[tuple[int, int]]] = []
    while remaining:
        root = min(remaining)
        remaining.remove(root)
        component = {root}
        pending = [root]
        while pending:
            x, y = pending.pop()
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        output.append(component)
    return tuple(sorted(output, key=lambda item: min(item)))


def build_boundary_product(
    family: FamilyDefinition,
    slice_definition: PairSliceDefinition,
    samples: Sequence[FamilySample],
    *,
    source_sample_revision: int,
    source_connectivity_revision: int,
    config: MultidimensionalPlannerConfig,
    product_revision: int = 1,
    global_probes_completed: int = 0,
    promotions_completed: int = 0,
    ray_states: Mapping[str, int] | None = None,
    authoritative_sample_keys: Iterable[str] | None = None,
    high_dimensional_fill_distance: tuple[float, float] | None = None,
) -> BoundaryProduct:
    x_grid = PairGrid.from_family(family, slice_definition.x_axis_key)
    y_grid = PairGrid.from_family(family, slice_definition.y_axis_key)
    fixed = slice_definition.fixed_parameters.as_mapping()
    by_grid: dict[tuple[int, int], FamilySample] = {}
    all_exact_points: set[tuple[int, int]] = set()
    authoritative = (
        None if authoritative_sample_keys is None
        else set(authoritative_sample_keys)
    )
    for sample in samples:
        values = sample.parameters.as_mapping()
        if any(values.get(key) != value for key, value in fixed.items()):
            continue
        try:
            point = (x_grid.index(values[slice_definition.x_axis_key]), y_grid.index(values[slice_definition.y_axis_key]))
        except (KeyError, ValueError):
            continue
        all_exact_points.add(point)
        if authoritative is not None and sample.sample_key not in authoritative:
            continue
        by_grid[point] = sample

    feasible_tiles_by_branch: dict[str, set[tuple[int, int]]] = {}
    not_found: set[tuple[int, int]] = set()
    mixed: set[tuple[int, int]] = set()
    unknown: set[tuple[int, int]] = set()
    candidate_cells = {
        (x, y)
        for point in by_grid
        for x in (point[0] - 1, point[0])
        for y in (point[1] - 1, point[1])
        if 0 <= x < x_grid.size and 0 <= y < y_grid.size
    }
    for x, y in sorted(candidate_cells):
            points = ((x, y), (x + 1, y), (x, y + 1), (x + 1, y + 1))
            corners = [by_grid.get(point) for point in points]
            if any(item is None for item in corners):
                unknown.add((x, y))
                continue
            concrete = [item for item in corners if item is not None]
            classifications = {item.classification for item in concrete}
            if SampleClassification.UNKNOWN in classifications:
                unknown.add((x, y))
            elif classifications == {SampleClassification.CONFIRMED_NOT_FOUND}:
                not_found.add((x, y))
            elif classifications == {SampleClassification.FEASIBLE_FOUND}:
                branches = {item.branch_id for item in concrete}
                if len(branches) == 1 and None not in branches:
                    feasible_tiles_by_branch.setdefault(str(next(iter(branches))), set()).add((x, y))
                else:
                    mixed.add((x, y))
            else:
                mixed.add((x, y))

    components: list[dict[str, Any]] = []
    for branch_id, cells in sorted(feasible_tiles_by_branch.items()):
        for component_cells in _connected_cell_components(cells):
            component_id = content_hash(
                {
                    "schema_version": BOUNDARY_PRODUCT_SCHEMA,
                    "slice_key": slice_definition.slice_key,
                    "branch_id": branch_id,
                    "cells": [list(item) for item in sorted(component_cells)],
                },
                prefix="emtg-family-feasible-component-v1",
            )
            components.append({
                "component_id": component_id,
                "branch_id": branch_id,
                "tiles": [list(item) for item in sorted(component_cells)],
                "rings": [
                    [list(point) for point in ring]
                    for ring in _component_rings(component_cells)
                ],
            })

    evidence = [
        ((x_grid.normalized(point[0]), y_grid.normalized(point[1])), sample)
        for point, sample in by_grid.items()
    ]
    predictions: list[dict[str, Any]] = []
    candidate_prediction_nodes = {
        (x, y)
        for point in by_grid
        for x in range(max(0, point[0] - 1), min(x_grid.size, point[0] + 1) + 1)
        for y in range(max(0, point[1] - 1), min(y_grid.size, point[1] + 1) + 1)
    }
    for x, y in sorted(candidate_prediction_nodes):
            if (x, y) in all_exact_points:
                continue
            probability = inverse_distance_prediction(
                (x_grid.normalized(x), y_grid.normalized(y)), evidence,
                neighbors=config.prediction_neighbors,
            )
            if probability is not None:
                predictions.append({
                    "schema_version": PREDICTION_SCHEMA,
                    "grid_index": [x, y],
                    "feasible_probability": probability,
                    "source_sample_revision": source_sample_revision,
                    "authoritative": False,
                })

    total_cells = x_grid.size * y_grid.size
    resolved = sum(len(value) for value in feasible_tiles_by_branch.values()) + len(not_found) + len(mixed)
    evaluated = len(by_grid)
    conclusive = sum(
        item.classification is not SampleClassification.UNKNOWN for item in by_grid.values()
    )
    unknown_nodes = evaluated - conclusive
    unresolved_cells = max(0, total_cells - resolved)
    if unresolved_cells:
        x_gap = max(
            (x_grid.normalized(index + 1) - x_grid.normalized(index)
             for index in range(x_grid.size)),
            default=Decimal(0),
        )
        y_gap = max(
            (y_grid.normalized(index + 1) - y_grid.normalized(index)
             for index in range(y_grid.size)),
            default=Decimal(0),
        )
        largest_unresolved = (x_gap * x_gap + y_gap * y_gap).sqrt()
    else:
        largest_unresolved = Decimal(0)
    coverage = CoverageSnapshot(
        slice_definition.slice_key,
        source_sample_revision,
        source_connectivity_revision,
        evaluated,
        conclusive,
        unknown_nodes,
        (x_grid.size + 1) * (y_grid.size + 1),
        total_cells,
        sum(len(value) for value in feasible_tiles_by_branch.values()),
        len(not_found),
        len(mixed),
        unresolved_cells,
        largest_unresolved,
        global_probes_completed,
        config.pair_global_probe_count,
        promotions_completed,
        config.pair_confirmation_budget,
        ray_states or {},
        None if high_dimensional_fill_distance is None else high_dimensional_fill_distance[0],
        None if high_dimensional_fill_distance is None else high_dimensional_fill_distance[1],
    )
    config_hash = content_hash(config.to_dict(), prefix="emtg-family-multidimensional-config-v1")
    exact = tuple(
        {
            "sample_key": sample.sample_key,
            "grid_index": list(point),
            "classification": sample.classification.value,
            "branch_id": sample.branch_id,
            "authoritative": True,
        }
        for point, sample in sorted(by_grid.items())
    )
    return BoundaryProduct(
        slice_definition.slice_key,
        product_revision,
        source_sample_revision,
        source_connectivity_revision,
        config_hash,
        slice_definition,
        exact,
        tuple(sorted(components, key=lambda item: item["component_id"])),
        tuple(sorted(not_found)),
        tuple(sorted(mixed)),
        tuple(sorted(unknown)),
        tuple(predictions),
        coverage,
    )


def anchor_pair_slices(
    family: FamilyDefinition, config: MultidimensionalPlannerConfig
) -> tuple[PairSliceDefinition, ...]:
    values = family.anchor_parameters.as_mapping()
    output = []
    for first, second in config.selected_pairs:
        fixed = {key: value for key, value in values.items() if key not in {first, second}}
        integer_keys = {
            item.key for item in family.anchor_parameters.values
            if isinstance(item.value, int) and item.key in fixed
        }
        output.append(PairSliceDefinition(
            family.family_id, first, second,
            ParameterVector.from_mapping(fixed, integer_keys),
        ))
    return tuple(output)


def logical_component_branches(
    family_id: str,
    samples: Sequence[FamilySample],
    accepted_edges: Iterable[tuple[str, str]],
    *,
    sample_rank: Mapping[str, tuple[int, int, str]],
    creation_revision: int,
) -> tuple[dict[str, str], tuple[BranchDefinition, ...]]:
    feasible = {
        sample.sample_key: sample for sample in samples
        if sample.classification is SampleClassification.FEASIBLE_FOUND
    }
    neighbors = {key: set() for key in feasible}
    for first, second in accepted_edges:
        if first in feasible and second in feasible:
            neighbors[first].add(second)
            neighbors[second].add(first)
    remaining = set(feasible)
    assignments: dict[str, str] = {}
    branches: list[BranchDefinition] = []
    while remaining:
        seed = min(remaining, key=lambda key: sample_rank.get(key, (10**12, 10**12, key)))
        pending = [seed]
        remaining.remove(seed)
        component = {seed}
        while pending:
            current = pending.pop()
            for neighbor in sorted(neighbors[current]):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    pending.append(neighbor)
        root = min(component, key=lambda key: sample_rank.get(key, (10**12, 10**12, key)))
        branch = BranchDefinition(family_id, root, creation_revision=creation_revision)
        branches.append(branch)
        assignments.update({key: branch.branch_id for key in component})
    return assignments, tuple(sorted(branches, key=lambda item: item.branch_id))


__all__ = [
    "BOUNDARY_PRODUCT_SCHEMA",
    "MULTIDIMENSIONAL_PLANNER_SCHEMA",
    "BoundaryProduct",
    "CellState",
    "CoverageSnapshot",
    "MultidimensionalPlannerConfig",
    "PairGrid",
    "PairSliceDefinition",
    "Prediction",
    "QuadtreeCell",
    "RayState",
    "SearchPreset",
    "anchor_pair_slices",
    "branch_evidence",
    "build_boundary_product",
    "classify_cell",
    "decision_vector_distance",
    "first_primes",
    "halton_directions",
    "halton_point",
    "inverse_distance_prediction",
    "logical_component_branches",
    "radical_inverse",
    "root_cells",
    "trajectory_distance",
]
