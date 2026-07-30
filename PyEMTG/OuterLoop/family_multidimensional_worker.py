"""Epoch coordinator for deterministic multidimensional family discovery."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
import math
from typing import Any, Iterable, Mapping, Sequence

from .atlas import (
    ContinuationEdge,
    FamilyDefinition,
    FamilySample,
    ParameterVector,
    SampleClassification,
)
from .canonical import content_hash
from .family_multidimensional import (
    BoundaryProduct,
    CellState,
    MultidimensionalPlannerConfig,
    PairGrid,
    PairSliceDefinition,
    QuadtreeCell,
    RayState,
    SearchPreset,
    anchor_pair_slices,
    branch_evidence,
    build_boundary_product,
    classify_cell,
    halton_directions,
    halton_point,
    logical_component_branches,
    root_cells,
)
from .family_storage import FamilyRunStore, StoredTask


@dataclass(frozen=True)
class _Proposal:
    category: str
    rank: tuple[Any, ...]
    task: StoredTask
    edge: ContinuationEdge | None


class MultidimensionalEpochPlanner:
    """Plan closed-epoch multidimensional work without observing completion order."""

    def __init__(
        self,
        family: FamilyDefinition,
        config: MultidimensionalPlannerConfig | None = None,
    ):
        self.family = family
        self.config = config or MultidimensionalPlannerConfig.from_family(family)
        self.axes = tuple(sorted(family.axes, key=lambda item: item.parameter_key))
        self.grids = {
            axis.parameter_key: PairGrid.from_family(family, axis.parameter_key)
            for axis in self.axes
        }
        self.slices = anchor_pair_slices(family, self.config)
        self.slices_by_key = {item.slice_key: item for item in self.slices}
        known_axes = {axis.parameter_key for axis in self.axes}
        known_parameters = set(family.anchor_parameters.as_mapping())
        for definition in self.slices:
            projected = {definition.x_axis_key, definition.y_axis_key}
            if projected - known_axes:
                raise ValueError("pair slice references unavailable axes")
            if set(definition.fixed_parameters.as_mapping()) != known_parameters - projected:
                raise ValueError(
                    "pair slice must fix every remaining family parameter exactly"
                )
            for key, value in definition.fixed_parameters.as_mapping().items():
                if key in self.grids:
                    self.grids[key].index(value)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    def initialize(self, store: FamilyRunStore, anchor_sample_key: str) -> None:
        if not self.enabled or store.multidimensional_initialized:
            return
        cells = []
        for item in self.slices:
            cells.extend(root_cells(
                item.slice_key,
                self.grids[item.x_axis_key],
                self.grids[item.y_axis_key],
            ))
        store.initialize_multidimensional(self.slices, cells)
        directions = halton_directions(
            self.config.high_dimensional_ray_count(len(self.axes)),
            len(self.axes),
        )
        store.initialize_rays(tuple(
            RayState(
                self.family.family_id, ordinal, direction, "scouting", anchor_sample_key,
            )
            for ordinal, direction in enumerate(directions)
        ))
        store.set_metadata("high_dimensional_probe_cursor", 1)

    def _slice_sample_map(
        self,
        store: FamilyRunStore,
        definition: PairSliceDefinition,
        samples: Sequence[FamilySample],
    ) -> dict[tuple[int, int], FamilySample]:
        authoritative_keys = self._slice_authoritative_keys(store, definition, samples)
        fixed = definition.fixed_parameters.as_mapping()
        x_grid = self.grids[definition.x_axis_key]
        y_grid = self.grids[definition.y_axis_key]
        output: dict[tuple[int, int], FamilySample] = {}
        for sample in samples:
            if sample.sample_key not in authoritative_keys:
                continue
            values = sample.parameters.as_mapping()
            if any(values.get(key) != value for key, value in fixed.items()):
                continue
            try:
                point = (
                    x_grid.index(values[definition.x_axis_key]),
                    y_grid.index(values[definition.y_axis_key]),
                )
            except (KeyError, ValueError):
                continue
            output[point] = sample
        return output

    def _slice_authoritative_keys(
        self,
        store: FamilyRunStore,
        definition: PairSliceDefinition,
        samples: Sequence[FamilySample],
    ) -> set[str]:
        creation_rows = tuple(
            row for row in store.multidimensional_task_rows()
            if bool(row["creates_sample"])
        )
        multidimensional_created = {
            str(row["sample_key"]) for row in creation_rows
        }
        slice_created = {
            str(row["sample_key"])
            for row in creation_rows
            if row["task"].get("slice_key") == definition.slice_key
        }
        return {
            sample.sample_key for sample in samples
            if (
                sample.sample_key not in multidimensional_created
                or sample.sample_key in slice_created
            )
        }

    def _high_dimensional_fill_distance(
        self, samples: Sequence[FamilySample]
    ) -> tuple[float, float] | None:
        if len(self.axes) < 3:
            return None
        evaluated = [self._normalized_parameters(sample) for sample in samples]
        if not evaluated:
            return None
        distances: list[float] = []
        for ordinal in range(1, 257):
            target = halton_point(ordinal, len(self.axes))
            distance = min(
                math.sqrt(sum(float(left - right) ** 2 for left, right in zip(target, point)))
                for point in evaluated
            )
            distances.append(distance)
        return max(distances), math.sqrt(sum(item * item for item in distances) / len(distances))

    def _normalized_parameters(self, sample: FamilySample) -> tuple[Decimal, ...]:
        return tuple(
            self.grids[axis.parameter_key].normalized(
                self.grids[axis.parameter_key].index(sample.parameters[axis.parameter_key])
            )
            for axis in self.axes
        )

    def _parameter_distance(self, first: FamilySample, second: FamilySample) -> Decimal:
        left, right = self._normalized_parameters(first), self._normalized_parameters(second)
        with localcontext() as context:
            context.prec = 60
            return sum((a - b) * (a - b) for a, b in zip(left, right)).sqrt()

    def _nearest_feasible(
        self, sample: FamilySample, feasible: Sequence[FamilySample]
    ) -> FamilySample:
        target = self._normalized_parameters(sample)

        def key(candidate: FamilySample) -> tuple[Decimal, str]:
            point = self._normalized_parameters(candidate)
            return sum((a - b) * (a - b) for a, b in zip(target, point)), candidate.sample_key

        if not feasible:
            raise ValueError("multidimensional planning requires a feasible seed")
        return min(feasible, key=key)

    def _ordered_seed_keys(
        self,
        sample: FamilySample,
        feasible: Sequence[FamilySample],
        preferred: Sequence[str] = (),
    ) -> list[str]:
        preferred_keys = [
            key for key in preferred
            if any(item.sample_key == key for item in feasible)
        ]
        remaining = [item for item in feasible if item.sample_key not in preferred_keys]
        remaining.sort(key=lambda item: (self._parameter_distance(sample, item), item.sample_key))
        return [*preferred_keys, *(item.sample_key for item in remaining)]

    def _sample(
        self,
        parameters: ParameterVector,
        *,
        epoch: int,
        parent: FamilySample,
    ) -> FamilySample:
        return FamilySample(
            self.family.family_id,
            self.family.architecture_id,
            self.family.comparison_context,
            parameters,
            self.family.comparison_context.fidelity,
            parent_sample_key=parent.sample_key,
            continuation_epoch=epoch,
        )

    def _task(
        self,
        sample: FamilySample,
        *,
        epoch: int,
        sequence: int,
        purpose: str,
        profile: str,
        creates_sample: bool,
        source_keys: Sequence[str],
        data: Mapping[str, Any],
        grid_index: int = 0,
    ) -> StoredTask:
        task_id = content_hash(
            {
                "schema_version": 1,
                "family_id": self.family.family_id,
                "sample_key": sample.sample_key,
                "purpose": purpose,
                "profile": profile,
                "origin": dict(data),
            },
            prefix="emtg-family-multidimensional-task-v1",
        )
        document = {
            "schema_version": 1,
            "task_id": task_id,
            "purpose": purpose,
            "evaluation_profile": profile,
            "creates_sample": creates_sample,
            "axis_keys": [axis.parameter_key for axis in self.axes],
            "chain_source_keys": list(source_keys),
            "feasible_seed_keys": list(source_keys),
            "grid_index": grid_index,
            **dict(data),
        }
        return StoredTask(sample, "", epoch, sequence, "refine", grid_index, document)

    def _edge(self, parent: FamilySample, child: FamilySample, kind: str, data: Mapping[str, Any]) -> ContinuationEdge:
        return ContinuationEdge(
            self.family.family_id,
            parent.sample_key,
            child.sample_key,
            kind,
            self._parameter_distance(parent, child),
            {
                "purpose": kind,
                "parent_classification": parent.classification.value,
                "child_classification": "planned",
                "connected_feasible": False,
                **dict(data),
            },
        )

    def _pair_sample(
        self,
        definition: PairSliceDefinition,
        point: tuple[int, int],
        *,
        epoch: int,
        feasible: Sequence[FamilySample],
        purpose: str,
        profile: str,
        rank: tuple[Any, ...],
        category: str,
        extra: Mapping[str, Any] | None = None,
    ) -> _Proposal:
        parameters = self.family.anchor_parameters.with_values({
            definition.x_axis_key: self.grids[definition.x_axis_key].value(point[0]),
            definition.y_axis_key: self.grids[definition.y_axis_key].value(point[1]),
        })
        provisional = FamilySample(
            self.family.family_id,
            self.family.architecture_id,
            self.family.comparison_context,
            parameters,
            self.family.comparison_context.fidelity,
            continuation_epoch=epoch,
        )
        parent = self._nearest_feasible(provisional, feasible)
        sample = replace(provisional, parent_sample_key=parent.sample_key)
        data = {
            "slice_key": definition.slice_key,
            "pair_key": definition.pair_key,
            "pair_axes": [definition.x_axis_key, definition.y_axis_key],
            "pair_grid_index": list(point),
            **dict(extra or {}),
        }
        task = self._task(
            sample,
            epoch=epoch,
            sequence=0,
            purpose=purpose,
            profile=profile,
            creates_sample=True,
            source_keys=self._ordered_seed_keys(sample, feasible, (parent.sample_key,)),
            data=data,
            grid_index=point[0] * (self.grids[definition.y_axis_key].size + 1) + point[1],
        )
        edge = self._edge(parent, sample, purpose, data)
        return _Proposal(category, rank, task, edge)

    def _promotion_proposals(
        self,
        store: FamilyRunStore,
        samples: Sequence[FamilySample],
        *,
        epoch: int,
    ) -> list[_Proposal]:
        by_key = {item.sample_key: item for item in samples}
        history = store.multidimensional_task_rows()
        already = {
            str(row["sample_key"]) for row in history if row["purpose"] == "confirmation"
        }
        slice_rows = {row["slice_key"]: row for row in store.multidimensional_slices()}
        cells_by_slice = {
            key: store.multidimensional_cells(key, active_only=True)
            for key in slice_rows
        }
        branch_suspect_segments = []
        for edge in store.ready_edges():
            if edge.kind.startswith("connectivity_") or edge.kind == "grid_neighbor":
                continue
            parent = by_key.get(edge.parent_sample_key)
            child = by_key.get(edge.child_sample_key)
            if (
                parent is not None and child is not None
                and parent.classification is SampleClassification.FEASIBLE_FOUND
                and child.classification is SampleClassification.FEASIBLE_FOUND
            ):
                branch_suspect_segments.append((
                    self._normalized_parameters(parent),
                    self._normalized_parameters(child),
                ))

        def lies_on_suspect_segment(sample: FamilySample) -> bool:
            point = self._normalized_parameters(sample)
            for first, second in branch_suspect_segments:
                ratios = [
                    (value - left) / (right - left)
                    for value, left, right in zip(point, first, second)
                    if right != left
                ]
                if not ratios or not Decimal(0) < ratios[0] < Decimal(1):
                    continue
                if any(abs(item - ratios[0]) > Decimal("1e-30") for item in ratios[1:]):
                    continue
                if all(
                    left != right or value == left
                    for value, left, right in zip(point, first, second)
                ):
                    return True
            return False
        output: list[_Proposal] = []
        for row in history:
            if row["state"] != "completed" or bool(row["creates_sample"]) is False:
                continue
            sample = by_key.get(str(row["sample_key"]))
            if sample is None or sample.classification is not SampleClassification.UNKNOWN:
                continue
            if sample.sample_key in already:
                continue
            task_data = row["task"]
            slice_key = task_data.get("slice_key")
            origin_ray = task_data.get("ray_id")
            eligible = False
            mixed_rank = 3
            if sample.parent_sample_key in by_key and (
                by_key[sample.parent_sample_key].classification
                is SampleClassification.FEASIBLE_FOUND
            ):
                eligible = True
                mixed_rank = 2
                suspect = branch_evidence(
                    by_key[sample.parent_sample_key], sample,
                    decision_threshold=self.config.decision_distance_threshold,
                    trajectory_threshold=self.config.trajectory_distance_threshold,
                )
                if bool(suspect["branch_split"]):
                    mixed_rank = 1
            if lies_on_suspect_segment(sample):
                eligible = True
                mixed_rank = min(mixed_rank, 1)
            if slice_key in self.slices_by_key:
                definition = self.slices_by_key[str(slice_key)]
                mapping = self._slice_sample_map(store, definition, samples)
                point = tuple(map(int, task_data.get("pair_grid_index", ())))
                for cell in cells_by_slice[str(slice_key)]:
                    if len(point) == 2 and (
                        cell.x_lower <= point[0] <= cell.x_upper
                        and cell.y_lower <= point[1] <= cell.y_upper
                    ):
                        stencil = [mapping[item] for item in cell.stencil() if item in mapping]
                        has_feasible = any(
                            item.classification is SampleClassification.FEASIBLE_FOUND
                            for item in stencil
                        )
                        has_negative = any(
                            item.classification is SampleClassification.CONFIRMED_NOT_FOUND
                            for item in stencil
                        )
                        eligible = eligible or has_feasible
                        if has_feasible and has_negative:
                            mixed_rank = 0
                if int(slice_rows[str(slice_key)]["promotion_count"]) >= self.config.pair_confirmation_budget:
                    eligible = False
            if origin_ray is not None:
                eligible = True
            if not eligible:
                continue
            nearest_distance = min(
                (
                    self._parameter_distance(sample, item)
                    for item in by_key.values()
                    if item.classification is SampleClassification.FEASIBLE_FOUND
                ),
                default=Decimal("Infinity"),
            )
            source_keys = tuple(map(str, task_data.get("feasible_seed_keys", ())))
            confirmation = self._task(
                sample,
                epoch=epoch,
                sequence=0,
                purpose="confirmation",
                profile="confirmation",
                creates_sample=False,
                source_keys=source_keys,
                data={
                    "slice_key": slice_key,
                    "ray_id": origin_ray,
                    "origin_task_id": row["task_id"],
                    "origin_purpose": row["purpose"],
                    "pair_grid_index": task_data.get("pair_grid_index"),
                    "radius": task_data.get("radius"),
                    "max_radius": task_data.get("max_radius"),
                },
            )
            output.append(_Proposal(
                "urgent",
                (
                    0, mixed_rank, float(nearest_distance),
                    str(slice_key or ""), sample.sample_key,
                ),
                confirmation, None,
            ))
        return output

    def _cell_proposals(
        self,
        store: FamilyRunStore,
        samples: Sequence[FamilySample],
        feasible: Sequence[FamilySample],
        *,
        epoch: int,
    ) -> tuple[list[_Proposal], list[_Proposal]]:
        urgent: list[_Proposal] = []
        gaps: list[_Proposal] = []
        existing_keys = {item.sample_key for item in samples}
        proposed_keys: set[str] = set()
        slice_rows = {row["slice_key"]: row for row in store.multidimensional_slices()}
        for definition in self.slices:
            row = slice_rows[definition.slice_key]
            remaining = self.config.pair_sample_budget - int(row["new_coordinate_count"])
            if remaining <= 0:
                store.update_slice_state(definition.slice_key, state="budget_exhausted")
                continue
            slice_proposed = 0
            mapping = self._slice_sample_map(store, definition, samples)
            x_grid = self.grids[definition.x_axis_key]
            y_grid = self.grids[definition.y_axis_key]
            cells = list(store.multidimensional_cells(definition.slice_key, active_only=True))
            for original in cells:
                promotion_available = int(row["promotion_count"]) < self.config.pair_confirmation_budget
                state = classify_cell(original, mapping, promotion_available=promotion_available)
                cell = replace(original, state=state)
                if cell != original:
                    store.update_cell(cell)
                candidates_cells = [cell]
                missing = [point for point in cell.stencil() if point not in mapping]
                should_split = (
                    not missing and not cell.terminal and (
                        state is CellState.MIXED
                        or (
                            state is CellState.HOMOGENEOUS_EVIDENCE
                            and cell.normalized_diagonal(x_grid, y_grid)
                            > self.config.homogeneous_diagonal_target
                        )
                    )
                )
                if should_split:
                    children = cell.split()
                    store.replace_cell_with_children(cell, children)
                    candidates_cells = list(children)
                for target_cell in candidates_cells:
                    target_missing = [
                        point for point in target_cell.stencil() if point not in mapping
                    ]
                    for point in target_missing:
                        parameters = self.family.anchor_parameters.with_values({
                            definition.x_axis_key: x_grid.value(point[0]),
                            definition.y_axis_key: y_grid.value(point[1]),
                        })
                        probe = FamilySample(
                            self.family.family_id, self.family.architecture_id,
                            self.family.comparison_context, parameters,
                            self.family.comparison_context.fidelity,
                        )
                        if probe.sample_key in existing_keys or probe.sample_key in proposed_keys:
                            continue
                        proposed_keys.add(probe.sample_key)
                        slice_proposed += 1
                        category = "urgent" if state is CellState.MIXED else "gap"
                        diagonal = target_cell.normalized_diagonal(x_grid, y_grid)
                        normalized_area = (
                            x_grid.normalized(target_cell.x_upper)
                            - x_grid.normalized(target_cell.x_lower)
                        ) * (
                            y_grid.normalized(target_cell.y_upper)
                            - y_grid.normalized(target_cell.y_lower)
                        )
                        rank = (
                            1 if category == "urgent" else 4,
                            definition.slice_key,
                            target_cell.level,
                            -float(diagonal),
                            -float(normalized_area),
                            point,
                        )
                        proposal = self._pair_sample(
                            definition, point, epoch=epoch, feasible=feasible,
                            purpose="quadtree_discovery", profile="discovery",
                            rank=rank, category=category,
                            extra={"cell_id": target_cell.cell_id, "cell_level": target_cell.level},
                        )
                        (urgent if category == "urgent" else gaps).append(proposal)
                        if slice_proposed >= remaining:
                            break
                    if slice_proposed >= remaining:
                        break
                if slice_proposed >= remaining:
                    break
        return urgent, gaps

    def _global_pair_proposals(
        self,
        store: FamilyRunStore,
        samples: Sequence[FamilySample],
        feasible: Sequence[FamilySample],
        *,
        epoch: int,
    ) -> list[_Proposal]:
        output: list[_Proposal] = []
        existing_keys = {item.sample_key for item in samples}
        used_by_slice: dict[str, set[int]] = {}
        for row in store.probes(kind="pair_global"):
            used_by_slice.setdefault(str(row["scope_key"]), set()).add(int(row["ordinal"]))
        rows = {row["slice_key"]: row for row in store.multidimensional_slices()}
        for definition in self.slices:
            row = rows[definition.slice_key]
            remaining_coordinates = self.config.pair_sample_budget - int(row["new_coordinate_count"])
            used = used_by_slice.get(definition.slice_key, set())
            needed = self.config.pair_global_probe_count - len(used)
            if needed <= 0 or remaining_coordinates <= 0:
                continue
            cursor = int(row["probe_cursor"])
            generated = 0
            consumed = 0
            attempts = 0
            turn_limit = min(needed, self.config.epoch_sample_limit)
            while consumed < turn_limit and attempts < 10000:
                ordinal = cursor
                cursor += 1
                attempts += 1
                if ordinal in used:
                    continue
                point_value = halton_point(ordinal, 2)
                point = (
                    self.grids[definition.x_axis_key].snap_normalized(point_value[0]),
                    self.grids[definition.y_axis_key].snap_normalized(point_value[1]),
                )
                parameters = self.family.anchor_parameters.with_values({
                    definition.x_axis_key: self.grids[definition.x_axis_key].value(point[0]),
                    definition.y_axis_key: self.grids[definition.y_axis_key].value(point[1]),
                })
                candidate = FamilySample(
                    self.family.family_id, self.family.architecture_id,
                    self.family.comparison_context, parameters,
                    self.family.comparison_context.fidelity,
                )
                probe_id = content_hash(
                    {"schema_version": 1, "scope_key": definition.slice_key,
                     "kind": "pair_global", "ordinal": ordinal},
                    prefix="emtg-family-global-probe-v1",
                )
                if candidate.sample_key in existing_keys or any(
                    proposal.task.sample_key == candidate.sample_key for proposal in output
                ):
                    store.save_probe(
                        probe_id=probe_id, scope_key=definition.slice_key,
                        kind="pair_global", ordinal=ordinal,
                        sample_key=candidate.sample_key, state="collision",
                        definition={"grid_indices": list(point)},
                    )
                    used.add(ordinal)
                    consumed += 1
                    continue
                output.append(self._pair_sample(
                    definition, point, epoch=epoch, feasible=feasible,
                    purpose="pair_global_probe", profile="global_discovery",
                    rank=(2, definition.slice_key, ordinal, point), category="global",
                    extra={
                        "probe_ordinal": ordinal, "probe_id": probe_id,
                        "probe_kind": "pair_global", "probe_scope": definition.slice_key,
                    },
                ))
                used.add(ordinal)
                generated += 1
                consumed += 1
                if generated >= remaining_coordinates:
                    break
        return output

    def _ray_target(
        self, ray: RayState,
    ) -> tuple[Decimal, tuple[int, ...], Decimal] | None:
        axes = self.axes
        anchor = tuple(self.grids[axis.parameter_key].normalized(self.grids[axis.parameter_key].anchor_index) for axis in axes)
        limits = []
        steps = []
        for axis, coordinate, direction in zip(axes, anchor, ray.direction):
            if direction > 0:
                limits.append((Decimal(1) - coordinate) / direction)
            elif direction < 0:
                limits.append((Decimal(0) - coordinate) / direction)
            size = self.grids[axis.parameter_key].size
            if direction != 0 and size > 0:
                steps.append(Decimal(1) / (Decimal(size) * abs(direction)))
        if not limits or not steps:
            return None
        max_radius = min(limits)
        if ray.state == "refining" and ray.not_found_radius is not None:
            radius = (ray.current_radius + ray.not_found_radius) / 2
        elif ray.previous_radius is None:
            first_change: list[Decimal] = []
            epsilon = Decimal("1e-40")
            for axis, coordinate, direction in zip(axes, anchor, ray.direction):
                if direction == 0:
                    continue
                grid = self.grids[axis.parameter_key]
                neighbor = grid.anchor_index + (1 if direction > 0 else -1)
                if not 0 <= neighbor <= grid.size:
                    continue
                midpoint = (coordinate + grid.normalized(neighbor)) / 2
                candidate = (midpoint - coordinate) / direction
                # Native-grid ties select the lower index.  Moving toward a
                # higher index therefore requires the smallest schema-frozen
                # positive displacement beyond the exact midpoint.
                if neighbor > grid.anchor_index:
                    candidate += epsilon
                if candidate > 0:
                    first_change.append(candidate)
            if not first_change:
                return None
            radius = min(max_radius, min(first_change))
        else:
            delta = ray.current_radius - ray.previous_radius
            radius = min(max_radius, ray.current_radius + Decimal(2) * delta)
        if radius <= ray.current_radius and ray.state != "refining":
            return None
        point = tuple(
            self.grids[axis.parameter_key].snap_normalized(coordinate + radius * direction)
            for axis, coordinate, direction in zip(axes, anchor, ray.direction)
        )
        return radius, point, max_radius

    def _ray_proposals(
        self,
        store: FamilyRunStore,
        samples: Sequence[FamilySample],
        feasible: Sequence[FamilySample],
        *,
        epoch: int,
    ) -> list[_Proposal]:
        if self.config.preset is not SearchPreset.DEEP:
            return []
        by_key = {item.sample_key: item for item in samples}
        output: list[_Proposal] = []
        for ray in store.rays():
            if ray.terminal:
                continue
            if ray.evaluated_target_count >= self.config.ray_sample_limit:
                store.update_ray(replace(ray, state="budget_exhausted"))
                continue
            target = self._ray_target(ray)
            if target is None:
                store.update_ray(replace(ray, state="bound_feasible"))
                continue
            radius, point, max_radius = target
            parameters = self.family.anchor_parameters.with_values({
                axis.parameter_key: self.grids[axis.parameter_key].value(index)
                for axis, index in zip(self.axes, point)
            })
            provisional = FamilySample(
                self.family.family_id, self.family.architecture_id,
                self.family.comparison_context, parameters,
                self.family.comparison_context.fidelity,
                continuation_epoch=epoch,
            )
            current = by_key[ray.current_feasible_sample_key]
            if provisional.sample_key == current.sample_key:
                store.update_ray(replace(
                    ray,
                    state="bracketed" if ray.state == "refining" else "bound_feasible",
                ))
                continue
            existing = by_key.get(provisional.sample_key)
            if existing is not None:
                if existing.classification is SampleClassification.UNKNOWN:
                    prior = {
                        str(row["sample_key"])
                        for row in store.multidimensional_task_rows()
                        if row["purpose"] == "confirmation"
                    }
                    if existing.sample_key not in prior:
                        task = self._task(
                            existing, epoch=epoch, sequence=0,
                            purpose="confirmation", profile="confirmation",
                            creates_sample=False,
                            source_keys=self._ordered_seed_keys(
                                existing,
                                feasible,
                                tuple(
                                    key for key in (
                                        ray.current_feasible_sample_key,
                                        ray.previous_feasible_sample_key,
                                    ) if key is not None
                                ),
                            ),
                            data={
                                "ray_id": ray.ray_id, "ray_ordinal": ray.ordinal,
                                "radius": str(radius), "max_radius": str(max_radius),
                                "origin_purpose": "high_dimensional_ray_collision",
                                "grid_indices": list(point),
                            },
                        )
                        output.append(_Proposal(
                            "ray", (3, ray.ordinal, float(radius), existing.sample_key),
                            task, None,
                        ))
                    continue
                self._apply_ray_outcome(store, ray, existing, radius, max_radius)
                continue
            sample = replace(provisional, parent_sample_key=current.sample_key)
            preferred = [current.sample_key]
            if ray.previous_feasible_sample_key is not None:
                preferred.append(ray.previous_feasible_sample_key)
            source_keys = self._ordered_seed_keys(sample, feasible, preferred)
            task = self._task(
                sample, epoch=epoch, sequence=0,
                purpose="high_dimensional_ray", profile="discovery", creates_sample=True,
                source_keys=source_keys,
                data={
                    "ray_id": ray.ray_id,
                    "ray_ordinal": ray.ordinal,
                    "radius": str(radius),
                    "max_radius": str(max_radius),
                    "ray_state": ray.state,
                    "grid_indices": list(point),
                    "radial_coordinate": [str(radius), str(ray.current_radius),
                                          None if ray.previous_radius is None else str(ray.previous_radius)],
                },
            )
            edge = self._edge(current, sample, "high_dimensional_ray", {
                "ray_id": ray.ray_id, "radius": str(radius),
            })
            output.append(_Proposal(
                "ray", (3, ray.ordinal, float(radius), sample.sample_key), task, edge,
            ))
        return output

    def _high_dimensional_global_proposals(
        self,
        store: FamilyRunStore,
        samples: Sequence[FamilySample],
        feasible: Sequence[FamilySample],
        *,
        epoch: int,
    ) -> list[_Proposal]:
        target_count = self.config.high_dimensional_global_probe_count(len(self.axes))
        if target_count == 0:
            return []
        used = {
            int(row["ordinal"])
            for row in store.probes(kind="high_dimensional_global")
        }
        remaining = target_count - len(used)
        if remaining <= 0:
            return []
        cursor = int(store.get_metadata("high_dimensional_probe_cursor", 1))
        existing = {item.sample_key for item in samples}
        output: list[_Proposal] = []
        consumed = 0
        attempts = 0
        turn_limit = min(remaining, self.config.epoch_sample_limit)
        while consumed < turn_limit and attempts < 10000:
            ordinal = cursor
            cursor += 1
            attempts += 1
            if ordinal in used:
                continue
            point_value = halton_point(ordinal, len(self.axes))
            indices = tuple(
                self.grids[axis.parameter_key].snap_normalized(value)
                for axis, value in zip(self.axes, point_value)
            )
            parameters = self.family.anchor_parameters.with_values({
                axis.parameter_key: self.grids[axis.parameter_key].value(index)
                for axis, index in zip(self.axes, indices)
            })
            provisional = FamilySample(
                self.family.family_id, self.family.architecture_id,
                self.family.comparison_context, parameters,
                self.family.comparison_context.fidelity,
                continuation_epoch=epoch,
            )
            probe_id = content_hash(
                {"schema_version": 1, "scope_key": self.family.family_id,
                 "kind": "high_dimensional_global", "ordinal": ordinal},
                prefix="emtg-family-global-probe-v1",
            )
            if provisional.sample_key in existing or any(
                item.task.sample_key == provisional.sample_key for item in output
            ):
                store.save_probe(
                    probe_id=probe_id, scope_key=self.family.family_id,
                    kind="high_dimensional_global", ordinal=ordinal,
                    sample_key=provisional.sample_key, state="collision",
                    definition={"grid_indices": list(indices)},
                )
                used.add(ordinal)
                consumed += 1
                continue
            parent = self._nearest_feasible(provisional, feasible)
            sample = replace(provisional, parent_sample_key=parent.sample_key)
            source_keys = self._ordered_seed_keys(
                sample, feasible, (parent.sample_key,)
            )
            task = self._task(
                sample, epoch=epoch, sequence=0,
                purpose="high_dimensional_global_probe", profile="global_discovery",
                creates_sample=True, source_keys=source_keys,
                data={
                    "probe_ordinal": ordinal, "grid_indices": list(indices),
                    "probe_id": probe_id, "probe_kind": "high_dimensional_global",
                    "probe_scope": self.family.family_id,
                },
            )
            edge = self._edge(parent, sample, "high_dimensional_global_probe", {
                "probe_ordinal": ordinal,
            })
            output.append(_Proposal(
                "global", (2, "high_dimensional", ordinal, indices), task, edge,
            ))
            used.add(ordinal)
            consumed += 1
        return output

    @staticmethod
    def _take(
        pool: list[_Proposal], count: int, selected: list[_Proposal], seen: set[str]
    ) -> None:
        selected_samples = {item.task.sample_key for item in selected}
        for proposal in sorted(pool, key=lambda item: (item.rank, item.task.sample_key)):
            if len(selected) >= count:
                return
            task_id = str(proposal.task.task["task_id"])
            if task_id in seen:
                continue
            if proposal.task.sample_key in selected_samples:
                continue
            seen.add(task_id)
            selected.append(proposal)
            selected_samples.add(proposal.task.sample_key)

    def plan_epoch(self, store: FamilyRunStore, epoch: int) -> int | None:
        if not self.enabled:
            return None
        samples = store.samples(completed_only=True)
        feasible = tuple(
            item for item in samples
            if item.classification is SampleClassification.FEASIBLE_FOUND
        )
        urgent = self._promotion_proposals(store, samples, epoch=epoch)
        boundary, gaps = self._cell_proposals(store, samples, feasible, epoch=epoch)
        urgent.extend(boundary)
        globals_pool = self._global_pair_proposals(store, samples, feasible, epoch=epoch)
        globals_pool.extend(
            self._high_dimensional_global_proposals(store, samples, feasible, epoch=epoch)
        )
        rays = self._ray_proposals(store, samples, feasible, epoch=epoch)
        limit = self.config.epoch_sample_limit
        selected: list[_Proposal] = []
        seen: set[str] = set()
        self._take(urgent, max(1, limit // 2), selected, seen)
        self._take(globals_pool, min(limit, len(selected) + max(1, limit // 4)), selected, seen)
        self._take(rays + gaps, limit, selected, seen)
        if len(selected) < limit:
            self._take(urgent + globals_pool + rays + gaps, limit, selected, seen)
        slice_remaining = {
            str(row["slice_key"]): max(
                0,
                self.config.pair_sample_budget - int(row["new_coordinate_count"]),
            )
            for row in store.multidimensional_slices()
        }
        promotion_remaining = {
            str(row["slice_key"]): max(
                0,
                self.config.pair_confirmation_budget - int(row["promotion_count"]),
            )
            for row in store.multidimensional_slices()
        }
        budgeted: list[_Proposal] = []
        for proposal in selected:
            slice_key = proposal.task.task.get("slice_key")
            consumes_coordinate = bool(
                proposal.task.task.get("creates_sample", True)
                and slice_key is not None
            )
            if consumes_coordinate:
                key = str(slice_key)
                if slice_remaining.get(key, 0) <= 0:
                    continue
                slice_remaining[key] -= 1
            if proposal.task.task.get("purpose") == "confirmation" and slice_key is not None:
                key = str(slice_key)
                if promotion_remaining.get(key, 0) <= 0:
                    continue
                promotion_remaining[key] -= 1
            budgeted.append(proposal)
        selected = budgeted
        if not selected:
            self._refresh_slice_completion(store, samples)
            # A coordinate may already belong to another authoritative slice
            # while sample identity forbids evaluating a duplicate row.  Such
            # evidence is deliberately not borrowed.  If no executable work
            # remains, close the affected slice as blocked so coverage keeps
            # the resulting cells explicitly unresolved instead of stalling
            # or fabricating a filled region.
            for row in store.multidimensional_slices():
                if row["state"] == "active":
                    store.update_slice_state(str(row["slice_key"]), state="blocked")
            return None
        tasks: list[StoredTask] = []
        edges: list[ContinuationEdge] = []
        for sequence, proposal in enumerate(selected):
            task = replace(proposal.task, epoch=epoch, chain_sequence=sequence)
            tasks.append(task)
            if proposal.edge is not None:
                edges.append(proposal.edge)
            slice_key = task.task.get("slice_key")
            if task.task.get("probe_ordinal") is not None:
                cursor = int(task.task["probe_ordinal"]) + 1
                if slice_key:
                    store.update_slice_state(str(slice_key), probe_cursor=cursor)
                elif task.task["purpose"] == "high_dimensional_global_probe":
                    store.set_metadata("high_dimensional_probe_cursor", cursor)
        store.plan_multidimensional_epoch(epoch, tasks, edges)
        for task in tasks:
            if task.task.get("probe_id") is None:
                continue
            store.save_probe(
                probe_id=str(task.task["probe_id"]),
                scope_key=str(task.task["probe_scope"]),
                kind=str(task.task["probe_kind"]),
                ordinal=int(task.task["probe_ordinal"]),
                sample_key=task.sample_key,
                state="planned",
                definition={
                    "grid_indices": task.task.get(
                        "pair_grid_index", task.task.get("grid_indices")
                    )
                },
            )
        return epoch

    def finalize_edge(self, edge: ContinuationEdge, sample: FamilySample) -> ContinuationEdge:
        return replace(edge, branch_evidence={
            **dict(edge.branch_evidence),
            "child_classification": sample.classification.value,
            "connected_feasible": sample.classification is SampleClassification.FEASIBLE_FOUND,
        })

    def _apply_ray_outcome(
        self,
        store: FamilyRunStore,
        ray: RayState,
        sample: FamilySample,
        radius: Decimal,
        max_radius: Decimal,
        *,
        increment_target: bool = True,
    ) -> None:
        count = ray.evaluated_target_count + (1 if increment_target else 0)
        if sample.classification is SampleClassification.UNKNOWN:
            updated = replace(ray, evaluated_target_count=count)
        elif sample.classification is SampleClassification.FEASIBLE_FOUND:
            at_bound = radius >= max_radius
            updated = replace(
                ray,
                state="bound_feasible" if at_bound else "scouting",
                previous_feasible_sample_key=ray.current_feasible_sample_key,
                previous_radius=ray.current_radius,
                current_feasible_sample_key=sample.sample_key,
                current_radius=radius,
                successful_scouts=ray.successful_scouts + 1,
                evaluated_target_count=count,
            )
        else:
            updated = replace(
                ray,
                state="refining",
                not_found_sample_key=sample.sample_key,
                not_found_radius=radius,
                refinement_count=ray.refinement_count + (1 if ray.state == "refining" else 0),
                evaluated_target_count=count,
            )
            target = self._ray_target(updated)
            if target is None or target[1] in {
                tuple(self.grids[axis.parameter_key].index(
                    store.sample(updated.current_feasible_sample_key).parameters[axis.parameter_key]
                ) for axis in self.axes),
                tuple(self.grids[axis.parameter_key].index(
                    sample.parameters[axis.parameter_key]
                ) for axis in self.axes),
            }:
                updated = replace(updated, state="bracketed")
        store.update_ray(updated)

    def _refresh_slice_completion(
        self, store: FamilyRunStore, samples: Sequence[FamilySample]
    ) -> None:
        rows = {row["slice_key"]: row for row in store.multidimensional_slices()}
        history = store.multidimensional_task_rows()
        for definition in self.slices:
            row = rows[definition.slice_key]
            mapping = self._slice_sample_map(store, definition, samples)
            x_grid = self.grids[definition.x_axis_key]
            y_grid = self.grids[definition.y_axis_key]
            promotion_available = int(row["promotion_count"]) < self.config.pair_confirmation_budget
            complete = True
            for original in store.multidimensional_cells(definition.slice_key, active_only=True):
                state = classify_cell(original, mapping, promotion_available=promotion_available)
                cell = replace(original, state=state)
                if cell != original:
                    store.update_cell(cell)
                missing = any(point not in mapping for point in cell.stencil())
                needs_split = (
                    not cell.terminal and (
                        state is CellState.MIXED
                        or (
                            state is CellState.HOMOGENEOUS_EVIDENCE
                            and cell.normalized_diagonal(x_grid, y_grid)
                            > self.config.homogeneous_diagonal_target
                        )
                    )
                )
                if missing or needs_split:
                    complete = False
                if state is CellState.UNRESOLVED and not promotion_available:
                    store.update_cell(replace(cell, state=CellState.UNKNOWN_BLOCKED))
            probe_count = sum(
                item["state"] in {"completed", "collision"}
                for item in store.probes(
                    scope_key=definition.slice_key, kind="pair_global"
                )
            )
            complete = complete and probe_count >= self.config.pair_global_probe_count
            if int(row["new_coordinate_count"]) >= self.config.pair_sample_budget:
                store.update_slice_state(definition.slice_key, state="budget_exhausted")
            elif complete:
                store.update_slice_state(definition.slice_key, state="complete")
            else:
                store.update_slice_state(definition.slice_key, state="active")

    def _recompute_connectivity(self, store: FamilyRunStore) -> None:
        samples = store.samples(completed_only=True)
        by_key = {item.sample_key: item for item in samples}
        confirmed_negative_points: list[tuple[int, ...]] = []
        unresolved_points: list[tuple[int, ...]] = []
        for sample in samples:
            if sample.classification is SampleClassification.FEASIBLE_FOUND:
                continue
            try:
                point = tuple(
                    self.grids[axis.parameter_key].index(
                        sample.parameters[axis.parameter_key]
                    )
                    for axis in self.axes
                )
            except (KeyError, ValueError):
                continue
            if sample.classification is SampleClassification.CONFIRMED_NOT_FOUND:
                confirmed_negative_points.append(point)
            else:
                unresolved_points.append(point)

        def crosses_points(
            parent: FamilySample, child: FamilySample, points: Sequence[tuple[int, ...]]
        ) -> bool:
            try:
                first = tuple(
                    self.grids[axis.parameter_key].index(
                        parent.parameters[axis.parameter_key]
                    )
                    for axis in self.axes
                )
                second = tuple(
                    self.grids[axis.parameter_key].index(
                        child.parameters[axis.parameter_key]
                    )
                    for axis in self.axes
                )
            except (KeyError, ValueError):
                return False
            if max(abs(left - right) for left, right in zip(first, second)) <= 1:
                return False
            return any(
                all(
                    min(left, right) <= value <= max(left, right)
                    for value, left, right in zip(point, first, second)
                )
                for point in points
            )

        evidence_edges: dict[str, tuple[ContinuationEdge, bool, Mapping[str, Any]]] = {}
        primary_edges = store.ready_edges()
        for edge in primary_edges:
            if edge.kind == "grid_neighbor" or edge.kind.startswith("connectivity_"):
                continue
            if edge.parent_sample_key not in by_key or edge.child_sample_key not in by_key:
                continue
            parent, child = by_key[edge.parent_sample_key], by_key[edge.child_sample_key]
            if (
                parent.classification is not SampleClassification.FEASIBLE_FOUND
                or child.classification is not SampleClassification.FEASIBLE_FOUND
            ):
                continue
            evidence = branch_evidence(
                parent, child,
                decision_threshold=self.config.decision_distance_threshold,
                trajectory_threshold=self.config.trajectory_distance_threshold,
            )
            global_edge = edge.kind in {
                "pair_global_probe", "high_dimensional_global_probe",
            }
            gap_crossing = crosses_points(parent, child, confirmed_negative_points)
            unresolved_crossing = crosses_points(parent, child, unresolved_points)
            evidence = {
                **evidence,
                "confirmed_gap_crossing": gap_crossing,
                "unresolved_gap_crossing": unresolved_crossing,
            }
            accepted = (
                not global_edge
                and not gap_crossing
                and not unresolved_crossing
                and not bool(evidence["branch_split"])
            )
            decision_distance = (
                Decimal(0)
                if evidence["decision_distance"] is None
                else Decimal(str(evidence["decision_distance"]))
            )
            updated = ContinuationEdge(
                edge.family_id, edge.parent_sample_key, edge.child_sample_key,
                f"connectivity_{edge.kind}", decision_distance,
                {**dict(edge.branch_evidence), **evidence, "source_edge_id": edge.edge_id},
            )
            evidence_edges[updated.edge_id] = (updated, accepted, evidence)

        # Unit-grid neighbors provide local evidence without joining across a gap.
        for definition in self.slices:
            mapping = self._slice_sample_map(store, definition, samples)
            for point, parent in sorted(mapping.items()):
                if parent.classification is not SampleClassification.FEASIBLE_FOUND:
                    continue
                for neighbor_point in ((point[0] + 1, point[1]), (point[0], point[1] + 1)):
                    child = mapping.get(neighbor_point)
                    if child is None or child.classification is not SampleClassification.FEASIBLE_FOUND:
                        continue
                    first, second = sorted((parent, child), key=lambda item: item.sample_key)
                    evidence = branch_evidence(
                        first, second,
                        decision_threshold=self.config.decision_distance_threshold,
                        trajectory_threshold=self.config.trajectory_distance_threshold,
                    )
                    edge = ContinuationEdge(
                        self.family.family_id, first.sample_key, second.sample_key,
                        "grid_neighbor",
                        (
                            Decimal(0)
                            if evidence["decision_distance"] is None
                            else Decimal(str(evidence["decision_distance"]))
                        ),
                        evidence,
                    )
                    evidence_edges[edge.edge_id] = (
                        edge, not bool(evidence["branch_split"]), evidence,
                    )
        # Deep full-box probes remain isolated until an evaluated native-grid
        # neighbor supplies actual local connectivity evidence.
        full_grid: dict[tuple[int, ...], FamilySample] = {}
        for sample in samples:
            if sample.classification is not SampleClassification.FEASIBLE_FOUND:
                continue
            try:
                point = tuple(
                    self.grids[axis.parameter_key].index(
                        sample.parameters[axis.parameter_key]
                    )
                    for axis in self.axes
                )
            except (KeyError, ValueError):
                continue
            full_grid[point] = sample
        for point, parent in sorted(full_grid.items()):
            for axis_ordinal in range(len(point)):
                neighbor_point = tuple(
                    value + (1 if index == axis_ordinal else 0)
                    for index, value in enumerate(point)
                )
                child = full_grid.get(neighbor_point)
                if child is None:
                    continue
                first, second = sorted((parent, child), key=lambda item: item.sample_key)
                evidence = branch_evidence(
                    first, second,
                    decision_threshold=self.config.decision_distance_threshold,
                    trajectory_threshold=self.config.trajectory_distance_threshold,
                )
                edge = ContinuationEdge(
                    self.family.family_id, first.sample_key, second.sample_key,
                    "connectivity_full_grid_neighbor",
                    (
                        Decimal(0)
                        if evidence["decision_distance"] is None
                        else Decimal(str(evidence["decision_distance"]))
                    ),
                    evidence,
                )
                evidence_edges[edge.edge_id] = (
                    edge, not bool(evidence["branch_split"]), evidence,
                )
        accepted_pairs = [
            (edge.parent_sample_key, edge.child_sample_key)
            for edge, accepted, _ in evidence_edges.values() if accepted
        ]
        ranks = {
            sample.sample_key: (sample.continuation_epoch, ordinal, sample.sample_key)
            for ordinal, sample in enumerate(samples)
        }
        assignments, proposed = logical_component_branches(
            self.family.family_id, samples, accepted_pairs,
            sample_rank=ranks, creation_revision=store.connectivity_revision + 1,
        )
        proposed_by_id = {item.branch_id: item for item in proposed}
        branch_ranks = {
            item.branch_id: ranks.get(item.root_sample_key, (10**12, 10**12, item.root_sample_key))
            for item in proposed
        }
        evidence_by_child: dict[str, tuple[str, set[str]]] = {}
        for edge, accepted, evidence in sorted(
            evidence_edges.values(), key=lambda item: item[0].edge_id
        ):
            if accepted or not bool(evidence.get("branch_split")):
                continue
            first_branch = assignments.get(edge.parent_sample_key)
            second_branch = assignments.get(edge.child_sample_key)
            if first_branch is None or second_branch is None or first_branch == second_branch:
                continue
            parent_branch, child_branch = sorted(
                (first_branch, second_branch), key=lambda key: (branch_ranks[key], key)
            )
            current_parent, edge_ids = evidence_by_child.get(
                child_branch, (parent_branch, set())
            )
            if (branch_ranks[parent_branch], parent_branch) < (
                branch_ranks[current_parent], current_parent
            ):
                current_parent = parent_branch
            edge_ids.add(edge.edge_id)
            evidence_by_child[child_branch] = current_parent, edge_ids
        for child_branch, (parent_branch, edge_ids) in evidence_by_child.items():
            branch = proposed_by_id[child_branch]
            proposed_by_id[child_branch] = replace(
                branch,
                parent_branch_id=parent_branch,
                evidence_edge_ids=tuple(sorted(edge_ids)),
            )
        proposed = tuple(proposed_by_id[key] for key in sorted(proposed_by_id))
        existing = {item.branch_id: item for item in store.branches()}
        branches = tuple(existing.get(item.branch_id, item) for item in proposed)
        store.save_connectivity(tuple(evidence_edges.values()), assignments, branches)

    def _build_products(self, store: FamilyRunStore) -> None:
        samples = store.samples(completed_only=True)
        history = store.multidimensional_task_rows()
        fill_distance = self._high_dimensional_fill_distance(samples)
        ray_states: dict[str, int] = {}
        for ray in store.rays():
            ray_states[ray.state] = ray_states.get(ray.state, 0) + 1
        for definition in self.slices:
            global_count = sum(
                row["state"] in {"completed", "collision"}
                for row in store.probes(
                    scope_key=definition.slice_key, kind="pair_global"
                )
            )
            promotion_count = sum(
                row["purpose"] == "confirmation"
                and row["task"].get("slice_key") == definition.slice_key
                and row["state"] == "completed"
                for row in history
            )
            revision = store.next_boundary_product_revision(definition.slice_key)
            product = build_boundary_product(
                self.family, definition, samples,
                source_sample_revision=store.sample_set_revision,
                source_connectivity_revision=store.connectivity_revision,
                config=self.config,
                product_revision=revision,
                global_probes_completed=global_count,
                promotions_completed=promotion_count,
                ray_states=ray_states,
                authoritative_sample_keys=self._slice_authoritative_keys(
                    store, definition, samples
                ),
                high_dimensional_fill_distance=fill_distance,
            )
            store.save_boundary_product(product)

    def close_epoch(self, store: FamilyRunStore, epoch: int) -> None:
        tasks = store.multidimensional_tasks_for_epoch(epoch, states=("completed",))
        for task in tasks:
            if task.task.get("probe_id") is not None:
                store.save_probe(
                    probe_id=str(task.task["probe_id"]),
                    scope_key=str(task.task["probe_scope"]),
                    kind=str(task.task["probe_kind"]),
                    ordinal=int(task.task["probe_ordinal"]),
                    sample_key=task.sample_key,
                    state="completed",
                    definition={
                        "grid_indices": task.task.get(
                            "pair_grid_index", task.task.get("grid_indices")
                        )
                    },
                )
            slice_key = task.task.get("slice_key")
            if slice_key:
                creates_coordinate = bool(task.task.get("creates_sample", True))
                store.update_slice_state(
                    str(slice_key),
                    coordinate_increment=(
                        1
                        if creates_coordinate
                        and not store.sample_resolved_entirely_from_cache(task.sample_key)
                        else 0
                    ),
                    promotion_increment=1 if task.task.get("purpose") == "confirmation" else 0,
                )
            ray_id = task.task.get("ray_id")
            if ray_id:
                ray = next(item for item in store.rays() if item.ray_id == ray_id)
                sample = store.sample(task.sample_key)
                if task.task.get("purpose") == "confirmation" and sample.classification is SampleClassification.UNKNOWN:
                    store.update_ray(replace(ray, state="blocked_unknown"))
                else:
                    self._apply_ray_outcome(
                        store, ray, sample,
                        Decimal(str(task.task["radius"])),
                        Decimal(str(task.task["max_radius"])),
                        increment_target=(
                            task.task.get("purpose") != "confirmation"
                            or task.task.get("origin_purpose")
                            == "high_dimensional_ray_collision"
                        ),
                    )
        store.close_epoch(epoch, store.chains())
        samples = store.samples(completed_only=True)
        self._refresh_slice_completion(store, samples)
        self._recompute_connectivity(store)
        self._build_products(store)

    def complete(self, store: FamilyRunStore) -> bool:
        if not self.enabled:
            return True
        slices_complete = all(
            row["state"] in {"complete", "budget_exhausted", "blocked"}
            for row in store.multidimensional_slices()
        )
        rays_complete = all(ray.terminal for ray in store.rays())
        target = self.config.high_dimensional_global_probe_count(len(self.axes))
        completed = sum(
            row["state"] in {"completed", "collision"}
            for row in store.probes(kind="high_dimensional_global")
        )
        return slices_complete and rays_complete and completed >= target


__all__ = ["MultidimensionalEpochPlanner"]
