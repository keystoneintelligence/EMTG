"""Atlas-only EMTG case mutation, seed transport, and rescue evaluation.

The ordinary OuterLoop path does not import or activate this module unless an
``atlas_case_v1`` context is explicitly supplied on an evaluation request.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from .atlas import (
    FamilyAttempt,
    FamilySample,
    FeasibilityPolicyRef,
    HardwareVariant,
    ParameterTransform,
    SampleClassification,
    decimal_value,
)
from .canonical import content_hash, file_sha256
from .hardware import (
    HardwareInspectionError,
    LaunchVehicleLibrary,
    PowerSystemLibrary,
    PropulsionSystemLibrary,
    SpacecraftInspection,
    resolve_hardware_reference,
)
from .model import (
    CandidateRecord,
    EvaluationRequest,
    EvaluationResult,
    EvaluationStatus,
)
from .parameters import (
    BOL_POWER,
    CONSTANT_THRUST,
    ELECTRIC_PROPELLANT_CAPACITY,
    ENGINE_COUNT,
    ENGINE_DUTY_CYCLE,
    LAUNCH_C3_INPUT,
    LAUNCH_EPOCH,
    MAXIMUM_MASS,
    THRUST_SCALE,
    ParameterApplicationPlan,
)
from .randomness import derive_seed
from .storage import ArtifactStore, EvaluationCache, exclusive_file_lock


ATLAS_CASE_SCHEMA = 1
HARDWARE_MATERIALIZER_VERSION = "atlas-hardware-v1"
TRIALX_TRANSPORT_VERSION = "atlas-trialx-v1"


class AtlasEvaluationError(ValueError):
    """Raised when an atlas case cannot be prepared without ambiguity."""


def _plain_decimal(value: Decimal | int) -> str | int:
    if isinstance(value, int):
        return value
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _numeric_text(value: Any) -> str:
    number = decimal_value(value)
    return str(_plain_decimal(number))


@dataclass(frozen=True)
class StagedHardwareFile:
    role: str
    sha256: str
    artifact_name: str
    staged_name: str
    option_field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "sha256": self.sha256,
            "artifact_name": self.artifact_name,
            "staged_name": self.staged_name,
            "option_field": self.option_field,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "StagedHardwareFile":
        return cls(
            str(value["role"]), str(value["sha256"]), str(value["artifact_name"]),
            str(value["staged_name"]),
            None if value.get("option_field") is None else str(value["option_field"]),
        )


@dataclass(frozen=True)
class HardwareVariantManifest:
    variant_id: str
    format_kind: str
    baseline_sha256: str
    transformations: tuple[Mapping[str, Any], ...]
    content_sha256: str
    artifact_name: str
    staged_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant_id": self.variant_id,
            "format_kind": self.format_kind,
            "baseline_sha256": self.baseline_sha256,
            "transformations": [dict(item) for item in self.transformations],
            "content_sha256": self.content_sha256,
            "artifact_name": self.artifact_name,
            "staged_name": self.staged_name,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "HardwareVariantManifest":
        return cls(
            str(value["variant_id"]), str(value["format_kind"]),
            str(value["baseline_sha256"]),
            tuple(MappingProxyType(dict(item)) for item in value.get("transformations", ())),
            str(value["content_sha256"]), str(value["artifact_name"]),
            str(value["staged_name"]),
        )


@dataclass(frozen=True)
class PreparedAtlasCase:
    architecture_id: str
    spacecraft_model_input: int
    application_plan: ParameterApplicationPlan
    files: tuple[StagedHardwareFile, ...]
    variants: tuple[HardwareVariantManifest, ...] = ()
    launch_performance: Mapping[str, Any] = field(default_factory=dict)
    architecture_change_allowed: bool = False
    materializer_version: str = HARDWARE_MATERIALIZER_VERSION

    def __post_init__(self) -> None:
        if not self.architecture_id:
            raise AtlasEvaluationError("prepared atlas case has no architecture_id")
        if self.spacecraft_model_input not in {0, 1, 2}:
            raise AtlasEvaluationError("prepared atlas case has unsupported spacecraft mode")
        roles = [item.role for item in self.files]
        if len(set(roles)) != len(roles):
            raise AtlasEvaluationError("prepared atlas case contains duplicate hardware roles")
        staged: dict[str, str] = {}
        for item in self.files:
            if item.staged_name in staged and staged[item.staged_name] != item.sha256:
                raise AtlasEvaluationError("prepared atlas case contains a staged filename collision")
            staged[item.staged_name] = item.sha256
        variant_ids = [item.variant_id for item in self.variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise AtlasEvaluationError("prepared atlas case contains duplicate variant IDs")
        file_hashes = {item.sha256 for item in self.files}
        if any(item.content_sha256 not in file_hashes for item in self.variants):
            raise AtlasEvaluationError("hardware variant manifest is not bound into the prepared case")
        object.__setattr__(self, "files", tuple(sorted(self.files, key=lambda item: item.role)))
        object.__setattr__(self, "variants", tuple(sorted(self.variants, key=lambda item: item.variant_id)))
        object.__setattr__(self, "launch_performance", MappingProxyType(dict(self.launch_performance)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CASE_SCHEMA,
            "architecture_id": self.architecture_id,
            "spacecraft_model_input": self.spacecraft_model_input,
            "application_plan": self.application_plan.to_dict(),
            "application_plan_hash": content_hash(
                self.application_plan.to_dict(), prefix="emtg-atlas-application-plan-v1"
            ),
            "files": [item.to_dict() for item in self.files],
            "variants": [item.to_dict() for item in self.variants],
            "launch_performance": dict(self.launch_performance),
            "architecture_change_allowed": self.architecture_change_allowed,
            "materializer_version": self.materializer_version,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PreparedAtlasCase":
        if value.get("schema_version") != ATLAS_CASE_SCHEMA:
            raise AtlasEvaluationError("unsupported prepared atlas case schema")
        if value.get("materializer_version", HARDWARE_MATERIALIZER_VERSION) != HARDWARE_MATERIALIZER_VERSION:
            raise AtlasEvaluationError("unsupported atlas hardware materializer version")
        plan = ParameterApplicationPlan.from_dict(value["application_plan"])
        expected = content_hash(plan.to_dict(), prefix="emtg-atlas-application-plan-v1")
        if value.get("application_plan_hash") != expected:
            raise AtlasEvaluationError("prepared atlas application-plan hash does not match")
        return cls(
            str(value["architecture_id"]), int(value["spacecraft_model_input"]), plan,
            tuple(StagedHardwareFile.from_dict(item) for item in value.get("files", ())),
            tuple(HardwareVariantManifest.from_dict(item) for item in value.get("variants", ())),
            dict(value.get("launch_performance", {})),
            bool(value.get("architecture_change_allowed", False)),
            str(value.get("materializer_version", HARDWARE_MATERIALIZER_VERSION)),
        )

    @property
    def identity(self) -> str:
        return content_hash(self.to_dict(), prefix="emtg-prepared-atlas-case-v1")


def atlas_candidate(candidate: CandidateRecord, prepared: PreparedAtlasCase) -> CandidateRecord:
    """Return a physical-input-specific candidate without changing topology."""
    mission = dict(candidate.phenotype.mission)
    mission["__atlas_case_v1__"] = prepared.to_dict()
    phenotype = replace(candidate.phenotype, mission=mission)
    return replace(candidate, phenotype=phenotype)


def _token_spans(line: str) -> list[re.Match[str]]:
    return list(re.finditer(r"[^,\s]+", line))


def _replace_token(line: str, index: int, replacement: str) -> str:
    spans = _token_spans(line)
    if index >= len(spans):
        raise AtlasEvaluationError("hardware row is shorter than its declared format")
    span = spans[index]
    return f"{line[:span.start()]}{replacement}{line[span.end():]}"


def _line_tokens(line: str) -> list[str]:
    return [match.group(0) for match in _token_spans(line)]


def _decode_hardware(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise AtlasEvaluationError(f"cannot decode hardware artifact {path}: {error}") from error


def _patch_hardware_text(text: str, variant: HardwareVariant) -> str:
    lines = text.splitlines(keepends=True)
    counts = {item.target: 0 for item in variant.transformations}
    in_power = variant.format_kind == "power_library"
    in_propulsion = variant.format_kind == "propulsion_library"
    for line_index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "#BeginStagePowerLibraryBlock":
            in_power, in_propulsion = True, False
            continue
        if stripped == "#BeginStagePropulsionLibraryBlock":
            in_power, in_propulsion = False, True
            continue
        if stripped == "#EndHardwareBlock":
            in_power = in_propulsion = False
            continue
        if not stripped or stripped.startswith("#"):
            continue
        tokens = _line_tokens(raw)
        if not tokens:
            continue
        updated = raw
        for transformation in variant.transformations:
            target = transformation.target
            pieces = target.split(":")
            token_index: int | None = None
            expected_name: str | None = None
            if target == "spacecraft:global_electric_propellant_capacity_kg":
                if tokens[0] == "GlobalElectricPropellantTankCapacity":
                    token_index = 1
            elif target == "spacecraft:global_electric_propellant_constraint:enable":
                if tokens[0] == "EnableGlobalElectricPropellantTankConstraint":
                    token_index = 1
            if token_index is None and len(pieces) == 4 and pieces[1] == "power_system":
                expected_name = pieces[2]
                if in_power and tokens[0] == expected_name and pieces[3] == "p0_kw":
                    token_index = 4
            if token_index is None and len(pieces) == 4 and pieces[1] == "propulsion_system":
                expected_name = pieces[2]
                fields = {"number_of_strings": 4, "constant_thrust_n": 7, "thrust_scale": 12}
                if in_propulsion and tokens[0] == expected_name:
                    token_index = fields.get(pieces[3])
            if token_index is not None:
                updated = _replace_token(updated, token_index, _numeric_text(transformation.value))
                tokens = _line_tokens(updated)
                counts[target] += 1
        lines[line_index] = updated
    missing = [target for target, count in counts.items() if count == 0]
    if missing:
        raise AtlasEvaluationError(f"hardware transformations matched no fields: {missing}")
    if variant.format_kind != "spacecraft_file":
        repeated = [target for target, count in counts.items() if count != 1]
        if repeated:
            raise AtlasEvaluationError(f"library transformations must match exactly once: {repeated}")
    return "".join(lines)


def _store_bytes(store: ArtifactStore, name: str, content: bytes) -> tuple[Path, str]:
    work = store.root.parent / ".atlas-materialization"
    work.mkdir(parents=True, exist_ok=True)
    destination = work / name
    lock = destination.with_suffix(destination.suffix + ".lock")
    with exclusive_file_lock(lock):
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{name}.", dir=work)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        stored, digest = store.put(destination)
    return stored, digest


def _validate_materialized(kind: str, path: Path) -> None:
    parsers = {
        "power_library": PowerSystemLibrary.from_file,
        "propulsion_library": PropulsionSystemLibrary.from_file,
        "spacecraft_file": SpacecraftInspection.from_file,
    }
    try:
        parsers[kind](path)
    except (KeyError, HardwareInspectionError) as error:
        raise AtlasEvaluationError(f"generated {kind} failed validation: {error}") from error


def _register_variant(store: ArtifactStore, variant_id: str, content_sha256: str) -> None:
    index = store.root / "atlas-variant-index" / f"{variant_id}.sha256"
    index.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(index.with_suffix(".lock")):
        if index.is_file():
            existing = index.read_text(encoding="ascii").strip()
            if existing != content_sha256:
                raise AtlasEvaluationError(
                    f"logical hardware variant {variant_id} materialized with conflicting content"
                )
            return
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{variant_id}.", dir=index.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
                stream.write(content_sha256 + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, index)
        finally:
            if temporary.exists():
                temporary.unlink()


class HardwareVariantMaterializer:
    """Generate immutable variants and the complete case-local hardware overlay."""

    _source_fields = {
        "power_library": "PowerSystemsLibraryFile",
        "propulsion_library": "PropulsionSystemsLibraryFile",
        "spacecraft_file": "SpacecraftOptionsFile",
    }

    def __init__(self, hardware_root: str | Path, artifact_store: ArtifactStore):
        self.hardware_root = Path(hardware_root).resolve()
        self.artifact_store = artifact_store

    def _resolve(self, reference: Any, role: str) -> Path:
        source = resolve_hardware_reference(self.hardware_root, str(reference))
        if source is None:
            raise AtlasEvaluationError(f"cannot resolve {role} artifact {reference!r}")
        return source

    def _package_active_throttles(
        self, text: str, kind: str, active_names: set[str]
    ) -> tuple[str, tuple[StagedHardwareFile, ...]]:
        if not active_names or kind not in {"propulsion_library", "spacecraft_file"}:
            return text, ()
        lines = text.splitlines(keepends=True)
        in_propulsion = kind == "propulsion_library"
        dependencies: dict[str, StagedHardwareFile] = {}
        matched: set[str] = set()
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            if stripped == "#BeginStagePropulsionLibraryBlock":
                in_propulsion = True
                continue
            if stripped == "#EndHardwareBlock":
                in_propulsion = False
                continue
            if not in_propulsion or not stripped or stripped.startswith("#"):
                continue
            tokens = _line_tokens(raw)
            if len(tokens) < 3 or tokens[0] not in active_names:
                continue
            matched.add(tokens[0])
            reference = tokens[2]
            source = resolve_hardware_reference(self.hardware_root, reference)
            mode = int(tokens[1])
            if source is None:
                if mode in {4, 5, 6, 7, 8, 9, 10}:
                    raise AtlasEvaluationError(
                        f"active propulsion system {tokens[0]!r} requires missing "
                        f"throttle table {reference!r}"
                    )
                continue
            stored, digest = self.artifact_store.put(source)
            staged_name = f"tt_{digest[:16]}_{source.name}"
            lines[index] = _replace_token(raw, 2, staged_name)
            dependencies.setdefault(digest, StagedHardwareFile(
                f"throttle_table:{tokens[0]}:{digest[:12]}", digest, stored.name,
                staged_name, None,
            ))
        missing = active_names - matched
        if missing:
            raise AtlasEvaluationError(
                f"active propulsion records were not found while packaging: {sorted(missing)}"
            )
        return "".join(lines), tuple(dependencies.values())

    def prepare(
        self,
        options: Any,
        plan: ParameterApplicationPlan,
        *,
        architecture_id: str,
        architecture_change_allowed: bool = False,
    ) -> PreparedAtlasCase:
        mode = int(getattr(options, "SpacecraftModelInput"))
        if mode not in {0, 1, 2}:
            raise AtlasEvaluationError(f"unsupported SpacecraftModelInput {mode}")
        if not architecture_change_allowed:
            for mutation in plan.mutations:
                if mutation.parameter_key == ENGINE_COUNT:
                    current = (
                        int(getattr(options, "number_of_electric_propulsion_systems"))
                        if mode != 1 else None
                    )
                    if current is None or int(mutation.value) != current:
                        raise AtlasEvaluationError("engine count changes require a new architecture stratum")

        files: list[StagedHardwareFile] = []
        manifests: list[HardwareVariantManifest] = []
        variant_by_kind = {variant.format_kind: variant for variant in plan.hardware_variants()}
        required: list[tuple[str, str]] = [
            ("launch_vehicle_library", "LaunchVehicleLibraryFile"),
        ]
        if mode == 0:
            required.extend((
                ("power_library", "PowerSystemsLibraryFile"),
                ("propulsion_library", "PropulsionSystemsLibraryFile"),
            ))
        elif mode == 1:
            required.append(("spacecraft_file", "SpacecraftOptionsFile"))

        for role, field_name in required:
            source = self._resolve(getattr(options, field_name), role)
            variant = variant_by_kind.get(role)
            materialized_text: str | None = None
            if variant is not None:
                if file_sha256(source) != variant.baseline.sha256:
                    raise AtlasEvaluationError(f"{role} baseline hash changed after parameter discovery")
                materialized_text = _patch_hardware_text(_decode_hardware(source), variant)
            if role in {"propulsion_library", "spacecraft_file"}:
                if mode == 0:
                    active_names = {str(getattr(options, "ElectricPropulsionSystemKey"))}
                else:
                    inspection = SpacecraftInspection.from_file(source)
                    active_names = {
                        item.name for item in inspection.effective_electric_propulsion_systems()
                    }
                materialized_text, throttle_files = self._package_active_throttles(
                    materialized_text if materialized_text is not None else _decode_hardware(source),
                    role, active_names,
                )
                files.extend(throttle_files)
            if materialized_text is not None and materialized_text.encode("utf-8") != source.read_bytes():
                artifact_stem = (
                    variant.variant_id if variant is not None
                    else content_hash(
                        {"source": file_sha256(source), "packaging": HARDWARE_MATERIALIZER_VERSION},
                        prefix="emtg-packaged-hardware-v1",
                    )
                )
                artifact_name = f"{artifact_stem}{source.suffix}"
                stored, digest = _store_bytes(
                    self.artifact_store, artifact_name, materialized_text.encode("utf-8")
                )
                _validate_materialized(role, stored)
                staged_name = f"{digest[:16]}_{source.name}"
                if variant is not None:
                    _register_variant(self.artifact_store, variant.variant_id, digest)
                    manifests.append(HardwareVariantManifest(
                        variant.variant_id, role, variant.baseline.sha256,
                        tuple(MappingProxyType(item.to_dict()) for item in variant.transformations),
                        digest, stored.name, staged_name,
                    ))
            else:
                stored, digest = self.artifact_store.put(source)
                staged_name = f"{digest[:16]}_{source.name}"
            files.append(StagedHardwareFile(
                role, digest, stored.name, staged_name, field_name
            ))

        if mode == 2:
            throttle_reference = str(getattr(options, "ThrottleTableFile", ""))
            throttle = resolve_hardware_reference(self.hardware_root, throttle_reference)
            if throttle is None and int(getattr(options, "engine_type", -1)) in {30, 31}:
                raise AtlasEvaluationError(
                    f"mission-options propulsion requires missing throttle table {throttle_reference!r}"
                )
            if throttle is not None:
                stored, digest = self.artifact_store.put(throttle)
                files.append(StagedHardwareFile(
                    "throttle_table", digest, stored.name,
                    f"tt_{digest[:16]}_{throttle.name}", "ThrottleTableFile",
                ))

        performance: dict[str, Any] = {}
        c3_mutation = next(
            (item for item in plan.mutations if item.parameter_key == LAUNCH_C3_INPUT), None
        )
        if c3_mutation is not None:
            launch_source = self._resolve(
                getattr(options, "LaunchVehicleLibraryFile"), "launch vehicle library"
            )
            vehicle = LaunchVehicleLibrary.from_file(launch_source).get(
                str(getattr(options, "LaunchVehicleKey"))
            )
            c3 = decimal_value(c3_mutation.value, where=LAUNCH_C3_INPUT)
            margin = decimal_value(getattr(options, "LV_margin", 0), where="LV_margin")
            delivered = vehicle.delivered_mass_kg(c3, margin)
            performance = {
                "launch_vehicle": vehicle.name,
                "c3_km2_s2": _numeric_text(c3),
                "c3_lower_km2_s2": _numeric_text(vehicle.c3_lower_km2_s2),
                "c3_upper_km2_s2": _numeric_text(vehicle.c3_upper_km2_s2),
                "delivered_mass_kg": _numeric_text(delivered),
            }

        return PreparedAtlasCase(
            architecture_id, mode, plan, tuple(files), tuple(manifests), performance,
            architecture_change_allowed,
        )


def _artifact_path(root: Path, item: StagedHardwareFile) -> Path:
    return root / item.sha256[:2] / item.sha256 / item.artifact_name


def _stage_file(source: Path, destination: Path, digest: str) -> None:
    if not source.is_file() or file_sha256(source) != digest:
        raise AtlasEvaluationError(f"content-addressed hardware artifact is missing or corrupt: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_file() or file_sha256(destination) != digest:
            raise AtlasEvaluationError(f"case-local hardware collision at {destination}")
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    if file_sha256(destination) != digest:
        raise AtlasEvaluationError(f"staged hardware failed hash verification: {destination}")


def _apply_parameter_plan(options: Any, plan: ParameterApplicationPlan, hardware_root: Path) -> None:
    first = options.Journeys[0]
    for mutation in plan.mutations:
        key = mutation.parameter_key
        value = mutation.value
        if key == LAUNCH_EPOCH:
            epoch = float(decimal_value(value, where=key))
            options.launch_window_open_date = epoch
            first.wait_time_bounds = [0.0, 0.0]
            if bool(getattr(first, "bounded_departure_date", False)):
                first.departure_date_bounds = [epoch, epoch]
        elif key == LAUNCH_C3_INPUT:
            if int(getattr(first, "departure_type", -1)) != 0 or int(
                getattr(first, "departure_class", -1)
            ) not in {0, 3}:
                raise AtlasEvaluationError("requested C3 is not launch-vehicle-backed")
            c3 = decimal_value(value, where=key)
            launch = LaunchVehicleLibrary.from_file(
                hardware_root / str(getattr(options, "LaunchVehicleLibraryFile"))
            ).get(str(getattr(options, "LaunchVehicleKey")))
            if c3 < launch.c3_lower_km2_s2 or c3 > launch.c3_upper_km2_s2:
                raise AtlasEvaluationError(
                    f"C3 {c3} is outside {launch.name} bounds "
                    f"[{launch.c3_lower_km2_s2}, {launch.c3_upper_km2_s2}]"
                )
            speed = math.sqrt(float(c3))
            first.initial_impulse_bounds = [speed, speed]
        elif key == MAXIMUM_MASS:
            options.maximum_mass = float(value)
        elif key == ELECTRIC_PROPELLANT_CAPACITY:
            if int(getattr(options, "SpacecraftModelInput")) != 1:
                options.maximum_electric_propellant = float(value)
                options.enable_electric_propellant_tank_constraint = 1
        elif key == ENGINE_DUTY_CYCLE:
            options.engine_duty_cycle = float(value)
        elif key == ENGINE_COUNT:
            if int(getattr(options, "SpacecraftModelInput")) != 1:
                options.number_of_electric_propulsion_systems = int(value)
        elif key == BOL_POWER:
            if int(getattr(options, "SpacecraftModelInput")) == 2:
                options.power_at_1_AU = float(value)
        elif key == CONSTANT_THRUST:
            if int(getattr(options, "SpacecraftModelInput")) == 2:
                options.Thrust = float(value)
        elif key == THRUST_SCALE:
            if int(getattr(options, "SpacecraftModelInput")) == 2:
                options.thrust_scale_factor = float(value)
        elif mutation.hardware_transformations:
            continue
        else:
            raise AtlasEvaluationError(f"no atlas case adapter exists for {key}")


def apply_prepared_atlas_case(
    options: Any,
    prepared_value: Mapping[str, Any],
    *,
    artifact_store_root: str | Path,
    case_directory: str | Path,
) -> None:
    prepared = PreparedAtlasCase.from_dict(prepared_value)
    if int(getattr(options, "SpacecraftModelInput")) != prepared.spacecraft_model_input:
        raise AtlasEvaluationError("SpacecraftModelInput changed after atlas discovery")
    overlay = Path(case_directory).resolve() / "hardware"
    root = Path(artifact_store_root).resolve()
    overlay.mkdir(parents=True, exist_ok=True)
    for item in prepared.files:
        _stage_file(_artifact_path(root, item), overlay / item.staged_name, item.sha256)
        if item.option_field is not None:
            setattr(options, item.option_field, item.staged_name)
    options.HardwarePath = str(overlay).replace("\\", "/") + "/"
    _apply_parameter_plan(options, prepared.application_plan, overlay)


def verify_written_atlas_case(path: str | Path, prepared_value: Mapping[str, Any]) -> None:
    """Reparse a generated options file and verify its requested physical inputs."""
    prepared = PreparedAtlasCase.from_dict(prepared_value)
    try:
        from .evaluator import _mission_options_module
        options = _mission_options_module().MissionOptions(str(Path(path).resolve()))
    except Exception as error:
        raise AtlasEvaluationError(f"generated atlas options cannot be reparsed: {error}") from error
    if not getattr(options, "success", 1):
        raise AtlasEvaluationError("generated atlas options failed MissionOptions parsing")
    if int(getattr(options, "SpacecraftModelInput")) != prepared.spacecraft_model_input:
        raise AtlasEvaluationError("written atlas case changed SpacecraftModelInput")
    overlay = Path(getattr(options, "HardwarePath")).resolve()
    for item in prepared.files:
        staged = overlay / item.staged_name
        if not staged.is_file() or file_sha256(staged) != item.sha256:
            raise AtlasEvaluationError(f"written atlas case has invalid {item.role} binding")
        if item.option_field is not None and str(getattr(options, item.option_field)) != item.staged_name:
            raise AtlasEvaluationError(f"written atlas case lost {item.option_field} binding")
    first = options.Journeys[0]
    mode = prepared.spacecraft_model_input
    for mutation in prepared.application_plan.mutations:
        expected = float(mutation.value)
        key = mutation.parameter_key
        if key == LAUNCH_EPOCH:
            if not math.isclose(float(options.launch_window_open_date), expected):
                raise AtlasEvaluationError("written atlas case lost launch epoch")
            if list(first.wait_time_bounds) != [0.0, 0.0]:
                raise AtlasEvaluationError("written atlas case does not fix launch wait time")
        elif key == LAUNCH_C3_INPUT:
            speed = math.sqrt(expected)
            if any(not math.isclose(float(item), speed) for item in first.initial_impulse_bounds):
                raise AtlasEvaluationError("written atlas case does not fix launch C3")
        elif key == MAXIMUM_MASS and not math.isclose(float(options.maximum_mass), expected):
            raise AtlasEvaluationError("written atlas case lost maximum mass")
        elif key == ELECTRIC_PROPELLANT_CAPACITY and mode != 1:
            if not math.isclose(float(options.maximum_electric_propellant), expected) or not int(
                options.enable_electric_propellant_tank_constraint
            ):
                raise AtlasEvaluationError("written atlas case lost electric propellant capacity")
        elif key == ENGINE_DUTY_CYCLE and not math.isclose(float(options.engine_duty_cycle), expected):
            raise AtlasEvaluationError("written atlas case lost duty cycle")
        elif key == ENGINE_COUNT and mode != 1 and int(
            options.number_of_electric_propulsion_systems
        ) != int(mutation.value):
            raise AtlasEvaluationError("written atlas case lost engine count")
        elif key == BOL_POWER and mode == 2 and not math.isclose(float(options.power_at_1_AU), expected):
            raise AtlasEvaluationError("written atlas case lost BOL power")
        elif key == CONSTANT_THRUST and mode == 2 and not math.isclose(float(options.Thrust), expected):
            raise AtlasEvaluationError("written atlas case lost constant thrust")
        elif key == THRUST_SCALE and mode == 2 and not math.isclose(
            float(options.thrust_scale_factor), expected
        ):
            raise AtlasEvaluationError("written atlas case lost thrust scale")


@dataclass(frozen=True)
class TrialXSeed:
    source_evaluation_key: str
    xdescriptions: tuple[str, ...]
    decision_vector: tuple[float, ...]
    lower_bounds: tuple[float, ...] = ()
    upper_bounds: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        size = len(self.xdescriptions)
        if not self.source_evaluation_key or not size or len(self.decision_vector) != size:
            raise AtlasEvaluationError("TrialX source descriptions/vector are incomplete")
        if len(set(self.xdescriptions)) != size:
            raise AtlasEvaluationError("TrialX source contains duplicate descriptions")
        if any(not math.isfinite(float(item)) for item in self.decision_vector):
            raise AtlasEvaluationError("TrialX source contains non-finite values")
        for name, bounds in (("lower", self.lower_bounds), ("upper", self.upper_bounds)):
            if bounds and len(bounds) != size:
                raise AtlasEvaluationError(f"TrialX {name} bounds have the wrong length")
            if any(not math.isfinite(float(item)) for item in bounds):
                raise AtlasEvaluationError(f"TrialX {name} bounds contain non-finite values")
        if self.lower_bounds and self.upper_bounds:
            for lower, upper in zip(self.lower_bounds, self.upper_bounds):
                if lower > upper:
                    raise AtlasEvaluationError("TrialX source bounds are reversed")

    @classmethod
    def from_result(
        cls, result: EvaluationResult, *, require_feasible: bool = True
    ) -> "TrialXSeed":
        if require_feasible and result.status is not EvaluationStatus.FEASIBLE:
            raise AtlasEvaluationError("continuation seeds must come from feasible evaluations")
        metrics = result.metrics
        return cls(
            result.evaluation_key,
            tuple(map(str, metrics.get("xdescriptions", ()))),
            tuple(float(item) for item in metrics.get("decision_vector", ())),
            tuple(float(item) for item in metrics.get("decision_vector_lower_bounds", ())),
            tuple(float(item) for item in metrics.get("decision_vector_upper_bounds", ())),
        )

    def initial_guess(self) -> dict[str, Any]:
        return {
            "seed_id": self.source_evaluation_key,
            "xdescriptions": self.xdescriptions,
            "decision_vector": self.decision_vector,
        }


@dataclass(frozen=True)
class ContinuationSeed:
    sample: FamilySample
    result: EvaluationResult
    trialx: TrialXSeed

    @classmethod
    def from_result(cls, sample: FamilySample, result: EvaluationResult) -> "ContinuationSeed":
        if sample.architecture_id == "":
            raise AtlasEvaluationError("seed sample has no architecture")
        return cls(sample, result, TrialXSeed.from_result(result))


@dataclass(frozen=True)
class SeedTransportManifest:
    method: str
    source_evaluation_keys: tuple[str, ...]
    secant_ratio: float | None
    adjusted_descriptions: tuple[str, ...]
    validations: Mapping[str, bool]
    transport_version: str = TRIALX_TRANSPORT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "source_evaluation_keys": list(self.source_evaluation_keys),
            "secant_ratio": self.secant_ratio,
            "adjusted_descriptions": list(self.adjusted_descriptions),
            "validations": dict(self.validations),
            "transport_version": self.transport_version,
        }


def _parameter_mapping(value: FamilySample | Mapping[str, Any]) -> dict[str, Decimal]:
    if isinstance(value, FamilySample):
        return {item.key: decimal_value(item.value) for item in value.parameters.values}
    return {str(key): decimal_value(item) for key, item in value.items()}


def _axis_coordinate(value: Any, transform: ParameterTransform) -> float:
    number = float(decimal_value(value))
    if transform is ParameterTransform.LOG10:
        if number <= 0:
            raise AtlasEvaluationError("logarithmic secant coordinates must be positive")
        return math.log10(number)
    return number


def _first_description_index(descriptions: Sequence[str], suffix: str) -> int:
    matches = [
        index for index, description in enumerate(descriptions)
        if re.match(r"^j0p0", description, re.I) and description.lower().endswith(suffix.lower())
    ]
    if len(matches) != 1:
        raise AtlasEvaluationError(
            f"TrialX requires exactly one first-departure {suffix!r} description; found {len(matches)}"
        )
    return matches[0]


def transport_trialx(
    parent: TrialXSeed,
    target_parameters: Mapping[str, Any],
    *,
    grandparent: TrialXSeed | None = None,
    axis_values: tuple[Any, Any, Any] | None = None,
    axis_transform: ParameterTransform = ParameterTransform.LINEAR,
    max_secant_ratio: float = 2.0,
) -> tuple[TrialXSeed, SeedTransportManifest]:
    """Transport an ordered TrialX vector without positional/fuzzy remapping."""
    vector = list(parent.decision_vector)
    method = "nearest"
    ratio: float | None = None
    sources = [parent.source_evaluation_key]
    if grandparent is not None and axis_values is not None:
        if grandparent.xdescriptions != parent.xdescriptions:
            raise AtlasEvaluationError("secant TrialX descriptions are not identical")
        target_axis, parent_axis, grand_axis = (
            _axis_coordinate(item, axis_transform) for item in axis_values
        )
        denominator = parent_axis - grand_axis
        candidate_ratio = (target_axis - parent_axis) / denominator if denominator else math.nan
        if math.isfinite(candidate_ratio) and 0 < candidate_ratio <= max_secant_ratio:
            vector = [
                current + candidate_ratio * (current - previous)
                for current, previous in zip(parent.decision_vector, grandparent.decision_vector)
            ]
            if any(not math.isfinite(item) for item in vector):
                raise AtlasEvaluationError("secant TrialX prediction is non-finite")
            method = "secant"
            ratio = candidate_ratio
            sources.append(grandparent.source_evaluation_key)

    adjusted: list[str] = []
    exempt: set[int] = set()
    if LAUNCH_EPOCH in target_parameters:
        index = _first_description_index(parent.xdescriptions, "event left state epoch")
        vector[index] = float(decimal_value(target_parameters[LAUNCH_EPOCH], where=LAUNCH_EPOCH))
        exempt.add(index)
        adjusted.append(parent.xdescriptions[index])
    if LAUNCH_C3_INPUT in target_parameters:
        c3 = decimal_value(target_parameters[LAUNCH_C3_INPUT], where=LAUNCH_C3_INPUT)
        if c3 < 0:
            raise AtlasEvaluationError("departure C3 cannot be negative")
        direct = [
            index for index, description in enumerate(parent.xdescriptions)
            if re.match(r"^j0p0", description, re.I)
            and description.lower().endswith("magnitude of outgoing velocity asymptote")
        ]
        periapse = [
            index for index, description in enumerate(parent.xdescriptions)
            if re.match(r"^j0p0", description, re.I)
            and description.lower().endswith("event left state vinfout")
        ]
        matches = direct + periapse
        if len(matches) != 1:
            raise AtlasEvaluationError(
                f"TrialX requires exactly one supported launch-C3 description; found {len(matches)}"
            )
        index = matches[0]
        vector[index] = math.sqrt(float(c3))
        exempt.add(index)
        adjusted.append(parent.xdescriptions[index])

    if parent.lower_bounds:
        outside = [
            parent.xdescriptions[index]
            for index, (value, lower) in enumerate(zip(vector, parent.lower_bounds))
            if index not in exempt and value < lower
        ]
        if outside:
            raise AtlasEvaluationError(f"predicted TrialX values are below source bounds: {outside}")
    if parent.upper_bounds:
        outside = [
            parent.xdescriptions[index]
            for index, (value, upper) in enumerate(zip(vector, parent.upper_bounds))
            if index not in exempt and value > upper
        ]
        if outside:
            raise AtlasEvaluationError(f"predicted TrialX values are above source bounds: {outside}")

    transported = TrialXSeed(
        parent.source_evaluation_key, parent.xdescriptions, tuple(vector),
        parent.lower_bounds, parent.upper_bounds,
    )
    manifest = SeedTransportManifest(
        method, tuple(sources), ratio, tuple(adjusted),
        MappingProxyType({
            "descriptions_unique": True,
            "descriptions_ordered": True,
            "values_finite": True,
            "source_bounds_satisfied": True,
        }),
    )
    return transported, manifest


def rank_feasible_seeds(
    target: FamilySample,
    seeds: Iterable[ContinuationSeed],
    *,
    normalization: Mapping[str, float] | None = None,
) -> tuple[ContinuationSeed, ...]:
    target_values = _parameter_mapping(target)
    scales = dict(normalization or {})

    def distance(seed: ContinuationSeed) -> tuple[Any, ...]:
        values = _parameter_mapping(seed.sample)
        keys = set(target_values) & set(values)
        total = 0.0
        for key in keys:
            scale = float(scales.get(key, max(1.0, abs(float(target_values[key])))))
            if scale <= 0 or not math.isfinite(scale):
                raise AtlasEvaluationError(f"invalid normalization scale for {key}")
            total += ((float(values[key]) - float(target_values[key])) / scale) ** 2
        branch_rank = 0 if target.branch_id and seed.sample.branch_id == target.branch_id else 1
        return branch_rank, math.sqrt(total), seed.sample.sample_key, seed.result.evaluation_key

    compatible = [
        seed for seed in seeds
        if seed.result.status is EvaluationStatus.FEASIBLE
        and seed.sample.architecture_id == target.architecture_id
        and seed.sample.comparison_context == target.comparison_context
        and seed.sample.fidelity == target.fidelity
    ]
    return tuple(sorted(compatible, key=distance))


@dataclass(frozen=True)
class RescuePolicy:
    direct_nlp_budget: Mapping[str, Any]
    expanded_nlp_budget: Mapping[str, Any]
    mbh_budget: Mapping[str, Any]
    alternate_seed_limit: int = 2
    mbh_confirmation_attempts: int = 2
    max_secant_ratio: float = 2.0
    quality_optimization: bool = False
    quality_metric: str | None = None
    quality_direction: str | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        if self.alternate_seed_limit < 0 or self.mbh_confirmation_attempts < 1:
            raise AtlasEvaluationError("rescue attempt counts are invalid")
        if not math.isfinite(self.max_secant_ratio) or self.max_secant_ratio <= 0:
            raise AtlasEvaluationError("max_secant_ratio must be positive and finite")
        if str(self.direct_nlp_budget.get("inner_loop", "")).lower() != "nlp":
            raise AtlasEvaluationError("direct rescue budget must use NLP")
        if str(self.expanded_nlp_budget.get("inner_loop", "")).lower() != "nlp":
            raise AtlasEvaluationError("expanded rescue budget must use NLP")
        if str(self.mbh_budget.get("inner_loop", "")).lower() != "mbh":
            raise AtlasEvaluationError("confirmation rescue budget must use MBH")
        if self.quality_optimization and (
            not self.quality_metric or self.quality_direction not in {"minimize", "maximize"}
        ):
            raise AtlasEvaluationError("quality mode requires a metric and direction")
        for name in ("direct_nlp_budget", "expanded_nlp_budget", "mbh_budget"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))

    @classmethod
    def balanced(cls, base_budget: Mapping[str, Any]) -> "RescuePolicy":
        if "nlp_max_run_time" not in base_budget or "nlp_major_iterations" not in base_budget:
            raise AtlasEvaluationError(
                "balanced rescue requires nlp_max_run_time and nlp_major_iterations"
            )
        direct = {**dict(base_budget), "inner_loop": "nlp"}
        expanded = {
            **direct,
            "nlp_max_run_time": 2 * float(direct["nlp_max_run_time"]),
            "nlp_major_iterations": 2 * int(direct["nlp_major_iterations"]),
        }
        mbh = {
            **dict(base_budget),
            "inner_loop": "mbh",
            "mbh_max_run_time": float(direct["nlp_max_run_time"]),
        }
        return cls(direct, expanded, mbh)

    @classmethod
    def from_ref(
        cls, reference: FeasibilityPolicyRef, base_budget: Mapping[str, Any]
    ) -> "RescuePolicy":
        settings = dict(reference.settings)
        default = cls.balanced(base_budget)
        return cls(
            settings.get("direct_nlp_budget", default.direct_nlp_budget),
            settings.get("expanded_nlp_budget", default.expanded_nlp_budget),
            settings.get("mbh_budget", default.mbh_budget),
            int(settings.get("alternate_seed_limit", default.alternate_seed_limit)),
            int(settings.get("mbh_confirmation_attempts", default.mbh_confirmation_attempts)),
            float(settings.get("max_secant_ratio", default.max_secant_ratio)),
            bool(settings.get("quality_optimization", default.quality_optimization)),
            settings.get("quality_metric"), settings.get("quality_direction"),
            reference.revision,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": "balanced",
            "revision": self.revision,
            "direct_nlp_budget": dict(self.direct_nlp_budget),
            "expanded_nlp_budget": dict(self.expanded_nlp_budget),
            "mbh_budget": dict(self.mbh_budget),
            "alternate_seed_limit": self.alternate_seed_limit,
            "mbh_confirmation_attempts": self.mbh_confirmation_attempts,
            "max_secant_ratio": self.max_secant_ratio,
            "quality_optimization": self.quality_optimization,
            "quality_metric": self.quality_metric,
            "quality_direction": self.quality_direction,
        }


@dataclass(frozen=True)
class SampleEvaluationOutcome:
    sample: FamilySample
    attempts: tuple[FamilyAttempt, ...]
    results: tuple[EvaluationResult, ...]


@dataclass(frozen=True)
class _AttemptEvidence:
    result: EvaluationResult
    family_attempt: FamilyAttempt
    transport_valid: bool
    stage: str


def _transport_valid(result: EvaluationResult, seed: TrialXSeed) -> bool:
    descriptions = tuple(map(str, result.metrics.get("xdescriptions", ())))
    if descriptions != seed.xdescriptions:
        return False
    vector = tuple(float(item) for item in result.metrics.get("decision_vector", ()))
    if len(vector) != len(descriptions) or any(not math.isfinite(item) for item in vector):
        return False
    lower = tuple(float(item) for item in result.metrics.get("decision_vector_lower_bounds", ()))
    upper = tuple(float(item) for item in result.metrics.get("decision_vector_upper_bounds", ()))
    if lower and (len(lower) != len(vector) or any(value < bound for value, bound in zip(vector, lower))):
        return False
    if upper and (len(upper) != len(vector) or any(value > bound for value, bound in zip(vector, upper))):
        return False
    stdout = result.artifacts.get("stdout")
    if stdout and Path(stdout).is_file():
        text = Path(stdout).read_text(encoding="utf-8", errors="replace")
        if "Initial guess missing value for:" in text:
            return False
    return True


class FamilySampleEvaluator:
    """Evaluate one sample with separately persisted continuation rescue attempts."""

    def __init__(
        self,
        base: Any,
        policy: RescuePolicy,
        *,
        cache: EvaluationCache | None = None,
        artifact_store: ArtifactStore | None = None,
        infrastructure_retries: int = 1,
    ):
        if infrastructure_retries < 0:
            raise AtlasEvaluationError("infrastructure retries cannot be negative")
        self.base = base
        self.policy = policy
        self.cache = cache
        self.artifact_store = artifact_store or getattr(base, "artifact_store", None)
        self.infrastructure_retries = infrastructure_retries

    def context_identity(self) -> Mapping[str, Any]:
        return {
            "type": "atlas_family_sample",
            "version": 1,
            "policy": self.policy.to_dict(),
            "base": self.base.context_identity(),
        }

    def _evaluate_request(
        self, request: EvaluationRequest, cancel_event: Any
    ) -> tuple[EvaluationResult, bool]:
        if self.cache is not None:
            cached = self.cache.get(request.evaluation_key)
            if cached is not None:
                return cached, True
        worker_attempts: list[dict[str, Any]] = []
        result: EvaluationResult | None = None
        for retry in range(self.infrastructure_retries + 1):
            result = self.base.evaluate(request, cancel_event)
            worker_attempts.append({
                "attempt": retry,
                "status": result.status.value,
                "reason": result.failure_reason,
            })
            transient = (
                result.status is EvaluationStatus.INFRASTRUCTURE_FAILED
                and result.provenance.get("transient", True) is True
            )
            if not transient or retry >= self.infrastructure_retries:
                break
            if cancel_event is not None and cancel_event.is_set():
                break
        assert result is not None
        provenance = dict(result.provenance)
        provenance["family_infrastructure_attempts"] = worker_attempts
        result = replace(result, provenance=provenance)
        stable = result.status not in {
            EvaluationStatus.PENDING,
            EvaluationStatus.RUNNING,
            EvaluationStatus.CANCELLED,
            EvaluationStatus.INFRASTRUCTURE_FAILED,
        }
        if self.cache is not None and stable:
            result = self.cache.put_frozen(result, request.context)
        return result, False

    def _manifest_artifact(self, payload: Mapping[str, Any], name: str) -> str | None:
        if self.artifact_store is None:
            return None
        from .canonical import canonical_json
        stored, _ = _store_bytes(
            self.artifact_store, name, (canonical_json(payload) + "\n").encode("utf-8")
        )
        return str(stored)

    def evaluate(
        self,
        sample: FamilySample,
        candidate: CandidateRecord,
        prepared: PreparedAtlasCase,
        feasible_seeds: Sequence[ContinuationSeed],
        *,
        chain_source_keys: Sequence[str] = (),
        axis_key: str | None = None,
        axis_transform: ParameterTransform = ParameterTransform.LINEAR,
        secant_coordinates: tuple[Any, Any, Any] | None = None,
        normalization: Mapping[str, float] | None = None,
        evaluation_profile: str = "full",
        previous_attempts: Sequence[FamilyAttempt] = (),
        previous_results: Sequence[EvaluationResult] = (),
        cancel_event: Any = None,
        attempt_observer: Callable[
            [EvaluationRequest, FamilyAttempt, EvaluationResult, bool], None
        ] | None = None,
    ) -> SampleEvaluationOutcome:
        if evaluation_profile not in {"full", "discovery", "global_discovery", "confirmation"}:
            raise AtlasEvaluationError(f"unsupported family evaluation profile {evaluation_profile!r}")
        if sample.architecture_id != prepared.architecture_id:
            raise AtlasEvaluationError("sample and prepared-case architectures differ")
        ranked = list(rank_feasible_seeds(sample, feasible_seeds, normalization=normalization))
        chain: list[ContinuationSeed] = []
        if chain_source_keys:
            by_key = {
                key: item for item in ranked
                for key in (item.result.evaluation_key, item.sample.sample_key)
            }
            chain = [by_key[key] for key in chain_source_keys if key in by_key]
            ranked = chain + [item for item in ranked if item not in chain]
        if not ranked:
            updated = replace(sample, classification=SampleClassification.UNKNOWN, confidence=None)
            return SampleEvaluationOutcome(updated, (), ())

        target_values = _parameter_mapping(sample)
        parent: ContinuationSeed | None = None
        grandparent = chain[1] if len(chain) > 1 else None
        primary_seed: TrialXSeed | None = None
        primary_manifest: SeedTransportManifest | None = None
        for candidate_seed in ranked:
            use_grandparent = grandparent if candidate_seed is (chain[0] if chain else None) else None
            axis_values = secant_coordinates if use_grandparent is not None else None
            if axis_values is None and use_grandparent is not None and axis_key is not None:
                axis_values = (
                    target_values[axis_key],
                    _parameter_mapping(candidate_seed.sample)[axis_key],
                    _parameter_mapping(use_grandparent.sample)[axis_key],
                )
            try:
                primary_seed, primary_manifest = transport_trialx(
                    candidate_seed.trialx, target_values,
                    grandparent=None if use_grandparent is None else use_grandparent.trialx,
                    axis_values=axis_values, axis_transform=axis_transform,
                    max_secant_ratio=self.policy.max_secant_ratio,
                )
            except AtlasEvaluationError:
                continue
            parent = candidate_seed
            break
        if parent is None or primary_seed is None or primary_manifest is None:
            updated = replace(sample, classification=SampleClassification.UNKNOWN, confidence=None)
            return SampleEvaluationOutcome(updated, (), ())
        physical_candidate = atlas_candidate(candidate, prepared)
        previous_results_by_key = {
            item.evaluation_key: item for item in previous_results
        }
        prior_evidence = [
            _AttemptEvidence(
                previous_results_by_key[item.evaluation_key], item,
                item.transport_valid is True,
                item.rescue_stage or f"legacy_attempt_{item.ordinal}",
            )
            for item in previous_attempts
            if item.evaluation_key in previous_results_by_key
        ]
        evidence: list[_AttemptEvidence] = []
        completed_stages = {
            (
                item.ordinal
                if item.rescue_stage_ordinal is None
                else item.rescue_stage_ordinal
            )
            for item in previous_attempts
        }
        aborted = False

        def run(stage: str, budget: Mapping[str, Any], seed: TrialXSeed,
                manifest: SeedTransportManifest, warm_key: str | None,
                stage_ordinal: int) -> bool:
            nonlocal aborted
            if stage_ordinal in completed_stages:
                return False
            evaluation_seed = derive_seed(
                prepared.architecture_id, sample.parameters.to_dict(),
                sample.comparison_context.comparison_context_id, self.policy.revision,
                stage, stage_ordinal, bits=31,
            )
            context = {
                "evaluator": self.base.context_identity(),
                "atlas_case_v1": prepared.to_dict(),
                "inner_seed_set": (evaluation_seed,),
                "family": {
                    "family_id": sample.family_id,
                    "sample_key": sample.sample_key,
                    "architecture_id": sample.architecture_id,
                    "branch_id": sample.branch_id,
                    "continuation_epoch": sample.continuation_epoch,
                    "comparison_context": sample.to_dict()["comparison_context"],
                    "coordinates": {
                        str(item["key"]): item["value"]
                        for item in sample.parameters.to_dict()["values"]
                    },
                },
            }
            request = EvaluationRequest(
                physical_candidate, sample.fidelity, evaluation_seed, dict(budget),
                seed.initial_guess(), context,
            )
            result, cache_hit = self._evaluate_request(request, cancel_event)
            transport_ok = _transport_valid(result, seed)
            payload = {
                "schema_version": 1,
                "sample_key": sample.sample_key,
                "stage": stage,
                "ordinal": stage_ordinal,
                "evaluation_key": result.evaluation_key,
                "transport": manifest.to_dict(),
                "post_run_transport_valid": transport_ok,
                "evaluation_seed": evaluation_seed,
                "budget": dict(budget),
            }
            artifacts = dict(result.artifacts)
            manifest_path = self._manifest_artifact(
                payload, f"attempt_{result.evaluation_key}.json"
            )
            if manifest_path is not None:
                artifacts["attempt_manifest"] = manifest_path
            attempt = FamilyAttempt(
                sample.sample_key, result.evaluation_key, result.candidate_id, stage_ordinal,
                warm_key, sample.fidelity, dict(budget), result.status,
                (
                    result.solver_violation
                    if result.solver_violation is not None
                    and math.isfinite(result.solver_violation) else None
                ),
                result.runtime_seconds, artifacts,
                rescue_stage_ordinal=stage_ordinal,
                rescue_stage=stage,
                transport_valid=transport_ok,
            )
            evidence.append(_AttemptEvidence(result, attempt, transport_ok, stage))
            if not (
                result.status is EvaluationStatus.CANCELLED
                and cancel_event is not None
                and cancel_event.is_set()
            ) and attempt_observer is not None:
                attempt_observer(request, attempt, result, cache_hit)
            if result.status in {
                EvaluationStatus.CANCELLED,
                EvaluationStatus.CONFIGURATION_FAILED,
                EvaluationStatus.INFRASTRUCTURE_FAILED,
                EvaluationStatus.STRUCTURALLY_INVALID,
            }:
                aborted = True
            return result.status is EvaluationStatus.FEASIBLE

        found = any(
            item.result.status is EvaluationStatus.FEASIBLE for item in prior_evidence
        )
        found = run(
            "primary_direct_nlp", self.policy.direct_nlp_budget,
            primary_seed, primary_manifest, parent.result.evaluation_key,
            0,
        ) or found
        if not aborted and (not found or self.policy.quality_optimization):
            alternates = [item for item in ranked if item is not parent]
            alternate_limit = (
                min(1, self.policy.alternate_seed_limit)
                if evaluation_profile in {"discovery", "global_discovery", "confirmation"}
                else self.policy.alternate_seed_limit
            )
            for index, alternate in enumerate(alternates[:alternate_limit]):
                try:
                    alternate_seed, alternate_manifest = transport_trialx(
                        alternate.trialx, target_values,
                        max_secant_ratio=self.policy.max_secant_ratio,
                    )
                except AtlasEvaluationError:
                    continue
                if run(
                    f"alternate_direct_nlp_{index}", self.policy.direct_nlp_budget,
                    alternate_seed, alternate_manifest, alternate.result.evaluation_key,
                    1 + index,
                ):
                    found = True
                    if not self.policy.quality_optimization:
                        break

        if (
            evaluation_profile in {"full", "confirmation"}
            and not aborted and (not found or self.policy.quality_optimization)
        ):
            valid_incumbents: list[tuple[float, EvaluationResult]] = []
            for item in (*prior_evidence, *evidence):
                try:
                    TrialXSeed.from_result(item.result, require_feasible=False)
                except AtlasEvaluationError:
                    continue
                violation = math.inf if item.result.solver_violation is None else item.result.solver_violation
                valid_incumbents.append((float(violation), item.result))
            incumbent_result = min(
                valid_incumbents, key=lambda item: (item[0], item[1].evaluation_key)
            )[1] if valid_incumbents else parent.result
            try:
                incumbent = TrialXSeed.from_result(incumbent_result, require_feasible=False)
                incumbent_seed, incumbent_manifest = transport_trialx(incumbent, target_values)
            except AtlasEvaluationError:
                incumbent_seed, incumbent_manifest = primary_seed, primary_manifest
                incumbent_result = parent.result
            if run(
                "expanded_direct_nlp", self.policy.expanded_nlp_budget,
                incumbent_seed, incumbent_manifest, incumbent_result.evaluation_key,
                1 + self.policy.alternate_seed_limit,
            ):
                found = True

        if (
            evaluation_profile in {"full", "confirmation", "global_discovery"}
            and not aborted and (not found or self.policy.quality_optimization)
        ):
            valid_results = []
            for item in (*prior_evidence, *evidence):
                try:
                    TrialXSeed.from_result(item.result, require_feasible=False)
                except AtlasEvaluationError:
                    continue
                violation = math.inf if item.result.solver_violation is None else item.result.solver_violation
                valid_results.append((float(violation), item.result))
            mbh_source = min(valid_results, key=lambda item: (item[0], item[1].evaluation_key))[1] if valid_results else parent.result
            try:
                mbh_trialx = TrialXSeed.from_result(mbh_source, require_feasible=False)
                mbh_seed, mbh_manifest = transport_trialx(mbh_trialx, target_values)
            except AtlasEvaluationError:
                mbh_seed, mbh_manifest = primary_seed, primary_manifest
                mbh_source = parent.result
            mbh_attempts = (
                1 if evaluation_profile == "global_discovery"
                else self.policy.mbh_confirmation_attempts
            )
            for index in range(mbh_attempts):
                if run(
                    f"mbh_confirmation_{index}", self.policy.mbh_budget,
                    mbh_seed, mbh_manifest, mbh_source.evaluation_key,
                    2 + self.policy.alternate_seed_limit + index,
                ):
                    found = True
                    if not self.policy.quality_optimization:
                        break

        feasible = [
            item for item in (*prior_evidence, *evidence)
            if item.result.status is EvaluationStatus.FEASIBLE
        ]
        if feasible:
            if self.policy.quality_optimization:
                metric = str(self.policy.quality_metric)
                def quality_key(item: _AttemptEvidence) -> tuple[float, str]:
                    raw = item.result.metrics.get(metric)
                    if raw is None or not math.isfinite(float(raw)):
                        return math.inf, item.result.evaluation_key
                    value = float(raw)
                    return (
                        -value if self.policy.quality_direction == "maximize" else value,
                        item.result.evaluation_key,
                    )
                best = min(
                    feasible,
                    key=quality_key,
                )
            else:
                best = feasible[0]
            updated = replace(
                sample, classification=SampleClassification.FEASIBLE_FOUND,
                confidence=1.0, best_evaluation_key=best.result.evaluation_key,
                metrics=best.result.metrics,
            )
        else:
            combined_evidence = (*prior_evidence, *evidence)
            conclusive = bool(combined_evidence) and all(
                item.result.status is EvaluationStatus.EMTG_INFEASIBLE
                and item.result.solver_violation is not None
                and math.isfinite(item.result.solver_violation)
                and item.transport_valid
                for item in combined_evidence
            )
            stage_names = {
                item.rescue_stage for item in previous_attempts
                if item.rescue_stage is not None
            } | {item.stage for item in evidence}
            mbh_count = sum(
                name.startswith("mbh_confirmation_") for name in stage_names
            )
            confirmed = (
                evaluation_profile in {"full", "confirmation"}
                and conclusive
                and "expanded_direct_nlp" in stage_names
                and mbh_count == self.policy.mbh_confirmation_attempts
            )
            valid_negative = [
                item for item in evidence
                if item.transport_valid and item.result.solver_violation is not None
                and math.isfinite(item.result.solver_violation)
            ]
            best = min(
                valid_negative,
                key=lambda item: (
                    float(item.result.solver_violation), item.family_attempt.ordinal,
                    item.result.evaluation_key,
                ),
            ) if valid_negative else None
            updated = replace(
                sample,
                classification=(
                    SampleClassification.CONFIRMED_NOT_FOUND
                    if confirmed else SampleClassification.UNKNOWN
                ),
                confidence=1.0 if confirmed else None,
                best_evaluation_key=(
                    sample.best_evaluation_key if best is None else best.result.evaluation_key
                ),
                metrics=sample.metrics if best is None else best.result.metrics,
            )
        return SampleEvaluationOutcome(
            updated,
            tuple(item.family_attempt for item in evidence),
            tuple(item.result for item in evidence),
        )
