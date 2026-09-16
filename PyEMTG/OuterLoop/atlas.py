"""Pure domain contracts for EMTG feasibility-atlas exploration.

The records in this module deliberately have no persistence, scheduling, or
viewer dependencies.  Identity-bearing records use the same canonical hashing
primitive as the existing OuterLoop, but have independent, versioned prefixes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .canonical import content_hash
from .model import ComparisonContext, EvaluationStatus


ATLAS_CONTRACT_SCHEMA = 1


class AtlasContractError(ValueError):
    """Raised when an atlas contract is malformed or non-canonical."""


class ParameterValueType(str, Enum):
    CONTINUOUS = "continuous"
    INTEGER = "integer"


class ParameterRole(str, Enum):
    INPUT = "input"
    REALIZED = "realized"


class ParameterScope(str, Enum):
    MISSION = "mission"
    FIRST_JOURNEY = "first_journey"
    LAUNCH_VEHICLE = "launch_vehicle"
    SPACECRAFT = "spacecraft"
    POWER_SYSTEM = "power_system"
    ELECTRIC_PROPULSION = "electric_propulsion"
    RESULT = "result"


class ParameterTransform(str, Enum):
    LINEAR = "linear"
    LOG10 = "log10"


class ParameterAvailability(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNOBSERVED = "unobserved"


class MutationStrategy(str, Enum):
    MISSION_OPTION = "mission_option"
    FIRST_DEPARTURE_EPOCH = "first_departure_epoch"
    FIRST_DEPARTURE_C3 = "first_departure_c3"
    HARDWARE_VARIANT = "hardware_variant"
    RESULT_OBSERVATION = "result_observation"


class SampleClassification(str, Enum):
    FEASIBLE_FOUND = "feasible_found"
    CONFIRMED_NOT_FOUND = "confirmed_not_found"
    UNKNOWN = "unknown"


NumericValue = Decimal | int


def decimal_value(value: Any, *, where: str = "value") -> Decimal:
    """Return a finite, canonical Decimal without accepting booleans."""
    if isinstance(value, bool):
        raise AtlasContractError(f"{where} must be numeric, not boolean")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise AtlasContractError(f"{where} is not a decimal") from error
    if not result.is_finite():
        raise AtlasContractError(f"{where} must be finite")
    return result.normalize() if result != 0 else Decimal(0)


def integer_value(value: Any, *, where: str = "value") -> int:
    """Return an exact integer; lossy float/string coercions are rejected."""
    if isinstance(value, bool):
        raise AtlasContractError(f"{where} must be an integer, not boolean")
    decimal = decimal_value(value, where=where)
    integral = decimal.to_integral_value()
    if decimal != integral:
        raise AtlasContractError(f"{where} must be an exact integer")
    return int(integral)


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _schema(data: Mapping[str, Any], kind: str) -> None:
    if data.get("schema_version") != ATLAS_CONTRACT_SCHEMA:
        raise AtlasContractError(f"{kind} schema must be {ATLAS_CONTRACT_SCHEMA}")


@dataclass(frozen=True)
class NumericBounds:
    lower: Decimal | None = None
    upper: Decimal | None = None

    def __post_init__(self) -> None:
        if self.lower is not None:
            object.__setattr__(self, "lower", decimal_value(self.lower, where="bounds.lower"))
        if self.upper is not None:
            object.__setattr__(self, "upper", decimal_value(self.upper, where="bounds.upper"))
        if self.lower is not None and self.upper is not None and self.lower > self.upper:
            raise AtlasContractError("numeric bounds are reversed")

    def contains(self, value: Decimal | int) -> bool:
        number = decimal_value(value)
        return not (
            (self.lower is not None and number < self.lower)
            or (self.upper is not None and number > self.upper)
        )

    def intersect(self, other: "NumericBounds | None") -> "NumericBounds":
        if other is None:
            return self
        lower_values = [value for value in (self.lower, other.lower) if value is not None]
        upper_values = [value for value in (self.upper, other.upper) if value is not None]
        return NumericBounds(max(lower_values) if lower_values else None,
                             min(upper_values) if upper_values else None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lower": None if self.lower is None else _decimal_text(self.lower),
            "upper": None if self.upper is None else _decimal_text(self.upper),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NumericBounds":
        return cls(data.get("lower"), data.get("upper"))


@dataclass(frozen=True)
class ParameterValue:
    key: str
    value: NumericValue

    def __post_init__(self) -> None:
        if not self.key:
            raise AtlasContractError("parameter key is empty")
        if isinstance(self.value, bool):
            raise AtlasContractError(f"parameter {self.key} cannot be boolean")
        if isinstance(self.value, int):
            return
        object.__setattr__(self, "value", decimal_value(self.value, where=self.key))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "numeric_type": "integer" if isinstance(self.value, int) else "decimal",
            "value": self.value if isinstance(self.value, int) else _decimal_text(self.value),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterValue":
        kind = data.get("numeric_type")
        if kind == "integer":
            value: NumericValue = integer_value(data["value"], where=str(data.get("key", "value")))
        elif kind == "decimal":
            value = decimal_value(data["value"], where=str(data.get("key", "value")))
        else:
            raise AtlasContractError("parameter value numeric_type is invalid")
        return cls(str(data["key"]), value)


@dataclass(frozen=True)
class ParameterVector:
    values: tuple[ParameterValue, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.values, key=lambda item: item.key))
        if len({item.key for item in ordered}) != len(ordered):
            raise AtlasContractError("parameter vector contains duplicate keys")
        object.__setattr__(self, "values", ordered)

    @classmethod
    def from_mapping(
        cls,
        values: Mapping[str, Any],
        integer_keys: Iterable[str] = (),
    ) -> "ParameterVector":
        integers = frozenset(integer_keys)
        return cls(tuple(
            ParameterValue(
                str(key),
                integer_value(value, where=str(key)) if key in integers else decimal_value(value, where=str(key)),
            )
            for key, value in values.items()
        ))

    def as_mapping(self) -> Mapping[str, NumericValue]:
        return MappingProxyType({item.key: item.value for item in self.values})

    def __contains__(self, key: object) -> bool:
        return any(item.key == key for item in self.values)

    def __getitem__(self, key: str) -> NumericValue:
        for item in self.values:
            if item.key == key:
                return item.value
        raise KeyError(key)

    def with_values(self, updates: Mapping[str, NumericValue]) -> "ParameterVector":
        merged = dict(self.as_mapping())
        unknown = set(updates) - set(merged)
        if unknown:
            raise AtlasContractError(f"parameter vector updates contain unknown keys: {sorted(unknown)}")
        merged.update(updates)
        integer_keys = {item.key for item in self.values if isinstance(item.value, int)}
        return ParameterVector.from_mapping(merged, integer_keys)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "values": [item.to_dict() for item in self.values],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ParameterVector":
        _schema(data, "parameter vector")
        return cls(tuple(ParameterValue.from_dict(item) for item in data.get("values", ())))


@dataclass(frozen=True)
class AxisDefinition:
    parameter_key: str
    lower: Decimal
    upper: Decimal
    resolution: Decimal
    transform: ParameterTransform = ParameterTransform.LINEAR

    def __post_init__(self) -> None:
        object.__setattr__(self, "lower", decimal_value(self.lower, where=f"{self.parameter_key}.lower"))
        object.__setattr__(self, "upper", decimal_value(self.upper, where=f"{self.parameter_key}.upper"))
        object.__setattr__(self, "resolution", decimal_value(self.resolution, where=f"{self.parameter_key}.resolution"))
        if not self.parameter_key:
            raise AtlasContractError("axis parameter key is empty")
        if self.lower > self.upper:
            raise AtlasContractError(f"axis {self.parameter_key} bounds are reversed")
        if self.resolution <= 0:
            raise AtlasContractError(f"axis {self.parameter_key} resolution must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.parameter_key,
            "lower": _decimal_text(self.lower),
            "upper": _decimal_text(self.upper),
            "resolution": _decimal_text(self.resolution),
            "transform": self.transform.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AxisDefinition":
        return cls(
            str(data["key"]), data["lower"], data["upper"], data["resolution"],
            ParameterTransform(data.get("transform", ParameterTransform.LINEAR.value)),
        )


@dataclass(frozen=True)
class AtlasDefinition:
    atlas_id: str
    name: str
    description: str = ""
    comparison_context_ids: tuple[str, ...] = ()
    family_ids_by_architecture: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.atlas_id.startswith("atlas-") or len(self.atlas_id) <= len("atlas-"):
            raise AtlasContractError("atlas_id must be an atlas-prefixed opaque identifier")
        object.__setattr__(self, "comparison_context_ids", tuple(sorted(set(self.comparison_context_ids))))
        strata: dict[str, tuple[str, ...]] = {}
        for architecture_id, family_ids in sorted(self.family_ids_by_architecture.items()):
            if not architecture_id:
                raise AtlasContractError("atlas stratum architecture_id is empty")
            values = tuple(sorted(set(str(value) for value in family_ids)))
            if not all(values):
                raise AtlasContractError("atlas stratum contains an empty family_id")
            strata[str(architecture_id)] = values
        object.__setattr__(self, "family_ids_by_architecture", MappingProxyType(strata))

    @classmethod
    def create(
        cls, name: str, description: str = "", comparison_context_ids: Iterable[str] = (),
    ) -> "AtlasDefinition":
        return cls(f"atlas-{uuid4().hex}", name, description, tuple(comparison_context_ids))

    def with_family(self, family: "FamilyDefinition") -> "AtlasDefinition":
        """Return an updated aggregate while preserving the atlas's opaque ID."""
        if family.atlas_id != self.atlas_id:
            raise AtlasContractError("family belongs to a different atlas")
        context_id = family.comparison_context.comparison_context_id
        if self.comparison_context_ids and context_id not in self.comparison_context_ids:
            raise AtlasContractError(
                f"comparison context {context_id!r} is not permitted by this atlas"
            )
        strata = {
            architecture_id: tuple(family_ids)
            for architecture_id, family_ids in self.family_ids_by_architecture.items()
        }
        strata[family.architecture_id] = (
            *strata.get(family.architecture_id, ()), family.family_id,
        )
        return AtlasDefinition(
            self.atlas_id, self.name, self.description,
            self.comparison_context_ids, strata,
        )

    def families_in_stratum(self, architecture_id: str) -> tuple[str, ...]:
        return self.family_ids_by_architecture.get(architecture_id, ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "atlas_id": self.atlas_id,
            "name": self.name,
            "description": self.description,
            "comparison_context_ids": list(self.comparison_context_ids),
            "family_ids_by_architecture": {
                architecture_id: list(family_ids)
                for architecture_id, family_ids in self.family_ids_by_architecture.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AtlasDefinition":
        _schema(data, "atlas")
        return cls(str(data["atlas_id"]), str(data.get("name", "")),
                   str(data.get("description", "")),
                   tuple(str(value) for value in data.get("comparison_context_ids", ())),
                   {
                       str(architecture_id): tuple(str(value) for value in family_ids)
                       for architecture_id, family_ids
                       in data.get("family_ids_by_architecture", {}).items()
                   })


@dataclass(frozen=True)
class HardwareBinding:
    category: str
    artifact_sha256: str
    selected_keys: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        digest = self.artifact_sha256.lower()
        if (
            not self.category or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise AtlasContractError("hardware binding requires category and SHA-256")
        object.__setattr__(self, "artifact_sha256", digest)
        object.__setattr__(self, "selected_keys", tuple(self.selected_keys))
        object.__setattr__(self, "details", _freeze(self.details))

    def identity_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "artifact_sha256": self.artifact_sha256.lower(),
            "selected_keys": list(self.selected_keys),
            "details": _plain(self.details),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardwareBinding":
        return cls(str(data["category"]), str(data["artifact_sha256"]),
                   tuple(str(value) for value in data.get("selected_keys", ())),
                   dict(data.get("details", {})))


@dataclass(frozen=True)
class ArchitectureSignature:
    topology: Mapping[str, Any]
    spacecraft_model_input: int
    hardware_bindings: tuple[HardwareBinding, ...]
    effective_thruster_modes: tuple[int, ...]
    engine_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "topology", _freeze(self.topology))
        object.__setattr__(self, "spacecraft_model_input",
                           integer_value(self.spacecraft_model_input, where="spacecraft_model_input"))
        object.__setattr__(self, "hardware_bindings",
                           tuple(sorted(self.hardware_bindings, key=lambda item: item.category)))
        if len({item.category for item in self.hardware_bindings}) != len(self.hardware_bindings):
            raise AtlasContractError("architecture contains duplicate hardware binding categories")
        object.__setattr__(self, "effective_thruster_modes",
                           tuple(integer_value(value, where="thruster mode") for value in self.effective_thruster_modes))
        object.__setattr__(self, "engine_count", integer_value(self.engine_count, where="engine_count"))
        if self.engine_count < 1:
            raise AtlasContractError("engine_count must be positive")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "topology": _plain(self.topology),
            "spacecraft_model_input": self.spacecraft_model_input,
            "hardware_bindings": [item.identity_dict() for item in self.hardware_bindings],
            "effective_thruster_modes": list(self.effective_thruster_modes),
            "engine_count": self.engine_count,
        }

    @property
    def architecture_id(self) -> str:
        return content_hash(self.identity_dict(), prefix="emtg-feasibility-architecture-v1")

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_dict(), "architecture_id": self.architecture_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitectureSignature":
        _schema(data, "architecture")
        value = cls(
            dict(data.get("topology", {})), int(data["spacecraft_model_input"]),
            tuple(HardwareBinding.from_dict(item) for item in data.get("hardware_bindings", ())),
            tuple(int(item) for item in data.get("effective_thruster_modes", ())),
            int(data["engine_count"]),
        )
        if data.get("architecture_id", value.architecture_id) != value.architecture_id:
            raise AtlasContractError("architecture_id does not match architecture content")
        return value


@dataclass(frozen=True)
class FeasibilityPolicyRef:
    name: str
    revision: int = 1
    settings: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise AtlasContractError("feasibility policy name is empty")
        if self.revision < 1:
            raise AtlasContractError("feasibility policy revision must be positive")
        object.__setattr__(self, "settings", _freeze(self.settings))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "revision": self.revision, "settings": _plain(self.settings)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FeasibilityPolicyRef":
        return cls(str(data["name"]), int(data.get("revision", 1)), dict(data.get("settings", {})))


def _comparison_to_dict(value: ComparisonContext) -> dict[str, Any]:
    return {
        "comparison_context_id": value.comparison_context_id,
        "trial": value.trial,
        "fidelity": value.fidelity,
    }


def _comparison_from_dict(data: Mapping[str, Any]) -> ComparisonContext:
    return ComparisonContext(str(data["comparison_context_id"]), int(data["trial"]), str(data["fidelity"]))


@dataclass(frozen=True)
class FamilyDefinition:
    atlas_id: str
    architecture_id: str
    anchor_candidate_id: str
    anchor_evaluation_key: str
    comparison_context: ComparisonContext
    anchor_parameters: ParameterVector
    axes: tuple[AxisDefinition, ...]
    feasibility_policy: FeasibilityPolicyRef
    planner_configuration: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.atlas_id.startswith("atlas-"):
            raise AtlasContractError("family atlas_id is invalid")
        if not all((self.architecture_id, self.anchor_candidate_id, self.anchor_evaluation_key)):
            raise AtlasContractError("family identity references cannot be empty")
        ordered = tuple(sorted(self.axes, key=lambda item: item.parameter_key))
        if len({item.parameter_key for item in ordered}) != len(ordered):
            raise AtlasContractError("family contains duplicate axes")
        missing = [item.parameter_key for item in ordered if item.parameter_key not in self.anchor_parameters]
        if missing:
            raise AtlasContractError(f"family axes are missing anchor parameters: {missing}")
        object.__setattr__(self, "axes", ordered)
        object.__setattr__(self, "planner_configuration", _freeze(self.planner_configuration))

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "atlas_id": self.atlas_id,
            "architecture_id": self.architecture_id,
            "anchor_candidate_id": self.anchor_candidate_id,
            "anchor_evaluation_key": self.anchor_evaluation_key,
            "comparison_context": _comparison_to_dict(self.comparison_context),
            "anchor_parameters": self.anchor_parameters.to_dict(),
            "axes": [axis.to_dict() for axis in self.axes],
            "feasibility_policy": self.feasibility_policy.to_dict(),
            "planner_configuration": _plain(self.planner_configuration),
        }

    @property
    def family_id(self) -> str:
        return "fam_" + content_hash(
            self.identity_dict(), prefix="emtg-feasibility-family-v1"
        )

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_dict(), "family_id": self.family_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FamilyDefinition":
        _schema(data, "family")
        value = cls(
            str(data["atlas_id"]), str(data["architecture_id"]),
            str(data["anchor_candidate_id"]), str(data["anchor_evaluation_key"]),
            _comparison_from_dict(data["comparison_context"]),
            ParameterVector.from_dict(data["anchor_parameters"]),
            tuple(AxisDefinition.from_dict(item) for item in data.get("axes", ())),
            FeasibilityPolicyRef.from_dict(data["feasibility_policy"]),
            dict(data.get("planner_configuration", {})),
        )
        if data.get("family_id", value.family_id) != value.family_id:
            raise AtlasContractError("family_id does not match family content")
        return value


@dataclass(frozen=True)
class FamilySample:
    family_id: str
    architecture_id: str
    comparison_context: ComparisonContext
    parameters: ParameterVector
    fidelity: str
    parent_sample_key: str | None = None
    continuation_epoch: int = 0
    branch_id: str | None = None
    classification: SampleClassification = SampleClassification.UNKNOWN
    confidence: float | None = None
    best_evaluation_key: str | None = None
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.continuation_epoch < 0:
            raise AtlasContractError("continuation epoch cannot be negative")
        if self.confidence is not None and (
            not math.isfinite(float(self.confidence)) or not 0.0 <= float(self.confidence) <= 1.0
        ):
            raise AtlasContractError("sample confidence must be finite and within [0, 1]")
        object.__setattr__(self, "metrics", _freeze(self.metrics))

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "family_id": self.family_id,
            "parameters": self.parameters.to_dict(),
            "comparison_context": _comparison_to_dict(self.comparison_context),
            "fidelity": self.fidelity,
        }

    @property
    def sample_key(self) -> str:
        return content_hash(self.identity_dict(), prefix="emtg-feasibility-sample-v1")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "sample_key": self.sample_key,
            "family_id": self.family_id,
            "architecture_id": self.architecture_id,
            "comparison_context": _comparison_to_dict(self.comparison_context),
            "parameters": self.parameters.to_dict(),
            "fidelity": self.fidelity,
            "parent_sample_key": self.parent_sample_key,
            "continuation_epoch": self.continuation_epoch,
            "branch_id": self.branch_id,
            "classification": self.classification.value,
            "confidence": self.confidence,
            "best_evaluation_key": self.best_evaluation_key,
            "metrics": _plain(self.metrics),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FamilySample":
        _schema(data, "family sample")
        value = cls(
            str(data["family_id"]), str(data["architecture_id"]),
            _comparison_from_dict(data["comparison_context"]),
            ParameterVector.from_dict(data["parameters"]), str(data["fidelity"]),
            data.get("parent_sample_key"), int(data.get("continuation_epoch", 0)),
            data.get("branch_id"), SampleClassification(data.get("classification", "unknown")),
            None if data.get("confidence") is None else float(data["confidence"]),
            data.get("best_evaluation_key"), dict(data.get("metrics", {})),
        )
        if data.get("sample_key", value.sample_key) != value.sample_key:
            raise AtlasContractError("sample_key does not match sample content")
        return value


@dataclass(frozen=True)
class FamilyAttempt:
    sample_key: str
    evaluation_key: str
    candidate_id: str
    ordinal: int
    warm_start_evaluation_key: str | None
    fidelity: str
    budget: Mapping[str, Any]
    status: EvaluationStatus
    violation: float | None = None
    runtime_seconds: float = 0.0
    artifacts: Mapping[str, str] = field(default_factory=dict)
    rescue_stage_ordinal: int | None = None
    rescue_stage: str | None = None
    transport_valid: bool | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.runtime_seconds < 0:
            raise AtlasContractError("attempt ordinal and runtime must be nonnegative")
        if self.rescue_stage_ordinal is not None and self.rescue_stage_ordinal < 0:
            raise AtlasContractError("rescue-stage ordinal must be nonnegative")
        if self.rescue_stage is not None and not self.rescue_stage:
            raise AtlasContractError("rescue stage cannot be empty")
        if self.violation is not None and not math.isfinite(float(self.violation)):
            raise AtlasContractError("attempt violation must be finite")
        object.__setattr__(self, "budget", _freeze(self.budget))
        object.__setattr__(self, "artifacts", _freeze(self.artifacts))

    @property
    def attempt_id(self) -> str:
        return self.evaluation_key

    @property
    def association_key(self) -> tuple[str, str]:
        return self.sample_key, self.evaluation_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "sample_key": self.sample_key,
            "evaluation_key": self.evaluation_key,
            "candidate_id": self.candidate_id,
            "ordinal": self.ordinal,
            "warm_start_evaluation_key": self.warm_start_evaluation_key,
            "fidelity": self.fidelity,
            "budget": _plain(self.budget),
            "status": self.status.value,
            "violation": self.violation,
            "runtime_seconds": self.runtime_seconds,
            "artifacts": _plain(self.artifacts),
            "rescue_stage_ordinal": self.rescue_stage_ordinal,
            "rescue_stage": self.rescue_stage,
            "transport_valid": self.transport_valid,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FamilyAttempt":
        _schema(data, "family attempt")
        return cls(
            str(data["sample_key"]), str(data["evaluation_key"]), str(data["candidate_id"]),
            int(data["ordinal"]), data.get("warm_start_evaluation_key"), str(data["fidelity"]),
            dict(data.get("budget", {})), EvaluationStatus(data["status"]),
            None if data.get("violation") is None else float(data["violation"]),
            float(data.get("runtime_seconds", 0.0)),
            {str(key): str(value) for key, value in data.get("artifacts", {}).items()},
            (
                None if data.get("rescue_stage_ordinal") is None
                else int(data["rescue_stage_ordinal"])
            ),
            data.get("rescue_stage"),
            (
                None if data.get("transport_valid") is None
                else bool(data["transport_valid"])
            ),
        )


@dataclass(frozen=True)
class BranchDefinition:
    family_id: str
    root_sample_key: str
    parent_branch_id: str | None = None
    creation_revision: int = 0
    evidence_edge_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.creation_revision < 0:
            raise AtlasContractError("branch creation revision cannot be negative")
        object.__setattr__(self, "evidence_edge_ids", tuple(sorted(set(self.evidence_edge_ids))))

    @property
    def branch_id(self) -> str:
        return content_hash(
            {"schema_version": ATLAS_CONTRACT_SCHEMA, "family_id": self.family_id,
             "root_sample_key": self.root_sample_key},
            prefix="emtg-feasibility-branch-v1",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "branch_id": self.branch_id,
            "family_id": self.family_id,
            "root_sample_key": self.root_sample_key,
            "parent_branch_id": self.parent_branch_id,
            "creation_revision": self.creation_revision,
            "evidence_edge_ids": list(self.evidence_edge_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BranchDefinition":
        _schema(data, "branch")
        value = cls(str(data["family_id"]), str(data["root_sample_key"]),
                    data.get("parent_branch_id"), int(data.get("creation_revision", 0)),
                    tuple(str(item) for item in data.get("evidence_edge_ids", ())))
        if data.get("branch_id", value.branch_id) != value.branch_id:
            raise AtlasContractError("branch_id does not match branch content")
        return value


@dataclass(frozen=True)
class ContinuationEdge:
    family_id: str
    parent_sample_key: str
    child_sample_key: str
    kind: str
    normalized_distance: Decimal
    branch_evidence: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "normalized_distance",
                           decimal_value(self.normalized_distance, where="normalized_distance"))
        if self.normalized_distance < 0:
            raise AtlasContractError("normalized edge distance cannot be negative")
        object.__setattr__(self, "branch_evidence", _freeze(self.branch_evidence))

    @property
    def edge_id(self) -> str:
        return content_hash(
            {"schema_version": ATLAS_CONTRACT_SCHEMA, "family_id": self.family_id,
             "parent_sample_key": self.parent_sample_key,
             "child_sample_key": self.child_sample_key, "kind": self.kind},
            prefix="emtg-feasibility-edge-v1",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "edge_id": self.edge_id,
            "family_id": self.family_id,
            "parent_sample_key": self.parent_sample_key,
            "child_sample_key": self.child_sample_key,
            "kind": self.kind,
            "normalized_distance": _decimal_text(self.normalized_distance),
            "branch_evidence": _plain(self.branch_evidence),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ContinuationEdge":
        _schema(data, "continuation edge")
        value = cls(str(data["family_id"]), str(data["parent_sample_key"]),
                    str(data["child_sample_key"]), str(data["kind"]),
                    data["normalized_distance"], dict(data.get("branch_evidence", {})))
        if data.get("edge_id", value.edge_id) != value.edge_id:
            raise AtlasContractError("edge_id does not match edge content")
        return value


@dataclass(frozen=True)
class HardwareArtifactIdentity:
    kind: str
    sha256: str

    def __post_init__(self) -> None:
        digest = self.sha256.lower()
        if not self.kind or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise AtlasContractError("hardware artifact identity requires a valid SHA-256")
        object.__setattr__(self, "sha256", digest)

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "sha256": self.sha256}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardwareArtifactIdentity":
        return cls(str(data["kind"]), str(data["sha256"]))


@dataclass(frozen=True)
class HardwareTransformation:
    parameter_key: str
    target: str
    value: NumericValue

    def __post_init__(self) -> None:
        if not self.parameter_key or not self.target:
            raise AtlasContractError("hardware transformation key and target are required")
        if isinstance(self.value, bool):
            raise AtlasContractError("hardware transformation value cannot be boolean")
        if not isinstance(self.value, int):
            object.__setattr__(self, "value", decimal_value(self.value, where=self.parameter_key))

    def to_dict(self) -> dict[str, Any]:
        return ParameterValue(self.parameter_key, self.value).to_dict() | {"target": self.target}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardwareTransformation":
        value = ParameterValue.from_dict(data)
        return cls(value.key, str(data["target"]), value.value)


@dataclass(frozen=True)
class HardwareVariant:
    format_kind: str
    baseline: HardwareArtifactIdentity
    transformations: tuple[HardwareTransformation, ...]

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.transformations, key=lambda item: (item.target, item.parameter_key)))
        targets = [item.target for item in ordered]
        if len(set(targets)) != len(targets):
            raise AtlasContractError("hardware variant has conflicting transformations")
        object.__setattr__(self, "transformations", ordered)

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATLAS_CONTRACT_SCHEMA,
            "format_kind": self.format_kind,
            "baseline": self.baseline.to_dict(),
            "transformations": [item.to_dict() for item in self.transformations],
        }

    @property
    def variant_id(self) -> str:
        return content_hash(self.identity_dict(), prefix="emtg-hardware-variant-v1")

    def to_dict(self) -> dict[str, Any]:
        return {**self.identity_dict(), "variant_id": self.variant_id}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HardwareVariant":
        _schema(data, "hardware variant")
        value = cls(str(data["format_kind"]), HardwareArtifactIdentity.from_dict(data["baseline"]),
                    tuple(HardwareTransformation.from_dict(item)
                          for item in data.get("transformations", ())))
        if data.get("variant_id", value.variant_id) != value.variant_id:
            raise AtlasContractError("variant_id does not match variant content")
        return value
