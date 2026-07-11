"""Sequence-aware initial-guess inventory and compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import threading
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .canonical import content_hash, file_sha256
from .model import MissionPhenotype
from .process import run_process


@dataclass(frozen=True)
class SeedFingerprint:
    endpoints: tuple[tuple[str, str], ...]
    flybys: tuple[str, ...]
    journey_count: int
    phase_types: tuple[Any, ...]
    launch_epoch: float | None
    flight_time: float | None
    hardware: tuple[tuple[str, Any], ...]
    phase_counts: tuple[int, ...] = ()
    dsm_counts: tuple[tuple[int, ...], ...] = ()
    boundary_types: tuple[tuple[Any, ...], ...] = ()
    constraint_structure: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_phenotype(cls, phenotype: MissionPhenotype) -> "SeedFingerprint":
        phase_types = tuple(
            journey.values.get("phase_type", tuple(phase.values.get("phase_type") for phase in journey.phases))
            for journey in phenotype.journeys
        )
        hardware_names = (
            "launch_vehicle", "spacecraft_configuration", "power_system",
            "electric_propulsion_system", "number_of_electric_propulsion_systems",
        )
        hardware = tuple(
            (name, phenotype.mission[name])
            for name in hardware_names
            if name in phenotype.mission
        )
        return cls(
            endpoints=tuple((journey.departure, journey.arrival) for journey in phenotype.journeys),
            flybys=tuple(body for journey in phenotype.journeys for body in journey.flybys),
            journey_count=len(phenotype.journeys),
            phase_types=phase_types,
            launch_epoch=_optional_float(phenotype.mission.get("launch_epoch")),
            flight_time=_optional_float(phenotype.mission.get("flight_time")),
            hardware=hardware,
            phase_counts=tuple(len(journey.phases) for journey in phenotype.journeys),
            dsm_counts=tuple(
                tuple(int(phase.values.get("dsm_count", phase.values.get("impulses_per_phase", 0))) for phase in journey.phases)
                for journey in phenotype.journeys
            ),
            boundary_types=tuple(
                tuple(journey.values.get(name) for name in ("departure_class", "departure_type", "arrival_class", "arrival_type"))
                for journey in phenotype.journeys
            ),
            constraint_structure=tuple(
                tuple(sorted(str(value) for value in journey.values.get("constraint_structure", ())))
                for journey in phenotype.journeys
            ),
        )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True)
class SeedArtifact:
    seed_id: str
    source: str
    fingerprint: SeedFingerprint
    xdescriptions: tuple[str, ...]
    decision_vector: tuple[float, ...]
    feasible: bool
    objective: float | None = None
    fidelity: str = "full"
    family: str | None = None
    metadata: Mapping[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if len(self.xdescriptions) != len(self.decision_vector):
            raise ValueError("seed descriptions and decision vector lengths differ")
        if self.metadata is None:
            object.__setattr__(self, "metadata", {})

    @classmethod
    def create(
        cls,
        source: str,
        phenotype: MissionPhenotype,
        xdescriptions: Sequence[str],
        decision_vector: Sequence[float],
        feasible: bool,
        **kwargs: Any,
    ) -> "SeedArtifact":
        fingerprint = SeedFingerprint.from_phenotype(phenotype)
        seed_id = content_hash(
            {
                "source": source,
                "fingerprint": fingerprint,
                "xdescriptions": tuple(xdescriptions),
                "decision_vector": tuple(float(value) for value in decision_vector),
            },
            prefix="emtg-outerloop-seed-v3",
        )
        return cls(seed_id, source, fingerprint, tuple(xdescriptions), tuple(map(float, decision_vector)), feasible, **kwargs)


@dataclass(frozen=True)
class SeedCompatibility:
    compatible: bool
    reason: str
    converter: str | None = None


class SeedConverter(Protocol):
    name: str

    def supports(self, seed: SeedArtifact, target: MissionPhenotype) -> bool: ...

    def convert(self, seed: SeedArtifact, target: MissionPhenotype) -> SeedArtifact: ...


class SeedProvider(Protocol):
    def generate(
        self, request: Mapping[str, Any], work_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> Mapping[str, Any]: ...


class ExactTransferConverter:
    name = "exact_descriptions"

    def compatibility(self, seed: SeedArtifact, target: MissionPhenotype, target_xdescriptions: Sequence[str] | None) -> SeedCompatibility:
        target_fp = SeedFingerprint.from_phenotype(target)
        checks = (
            (seed.fingerprint.endpoints == target_fp.endpoints, "endpoints differ"),
            (seed.fingerprint.flybys == target_fp.flybys, "flyby sequences differ"),
            (seed.fingerprint.journey_count == target_fp.journey_count, "journey counts differ"),
            (seed.fingerprint.phase_counts == target_fp.phase_counts, "phase counts differ"),
            (seed.fingerprint.phase_types == target_fp.phase_types, "transcriptions differ"),
            (seed.fingerprint.dsm_counts == target_fp.dsm_counts, "DSM counts differ"),
            (seed.fingerprint.boundary_types == target_fp.boundary_types, "boundary classes/types differ"),
            (seed.fingerprint.constraint_structure == target_fp.constraint_structure, "constraint structures differ"),
            (seed.fingerprint.hardware == target_fp.hardware, "hardware differs"),
        )
        for matches, reason in checks:
            if not matches:
                return SeedCompatibility(False, reason, self.name)
        if target_xdescriptions is None:
            return SeedCompatibility(False, "target ordered Xdescriptions are unavailable", self.name)
        if seed.xdescriptions != tuple(target_xdescriptions):
            return SeedCompatibility(False, "ordered Xdescriptions differ", self.name)
        return SeedCompatibility(True, "ordered Xdescriptions are identical", self.name)

    def supports(self, seed: SeedArtifact, target: MissionPhenotype) -> bool:
        return False  # target-native descriptions are mandatory

    def convert(
        self, seed: SeedArtifact, target: MissionPhenotype,
        target_xdescriptions: Sequence[str] | None = None,
    ) -> SeedArtifact:
        return seed


class SameShapeBodySubstitutionConverter(ExactTransferConverter):
    name = "same_shape_body_substitution"

    def compatibility(self, seed: SeedArtifact, target: MissionPhenotype, target_xdescriptions: Sequence[str] | None) -> SeedCompatibility:
        target_fp = SeedFingerprint.from_phenotype(target)
        checks = (
            (seed.fingerprint.journey_count == target_fp.journey_count, "journey counts differ"),
            (len(seed.fingerprint.flybys) == len(target_fp.flybys), "flyby/phase counts differ"),
            (seed.fingerprint.phase_counts == target_fp.phase_counts, "phase counts differ"),
            (seed.fingerprint.phase_types == target_fp.phase_types, "transcriptions differ"),
            (seed.fingerprint.dsm_counts == target_fp.dsm_counts, "DSM counts differ"),
            (seed.fingerprint.boundary_types == target_fp.boundary_types, "boundary classes/types differ"),
            (seed.fingerprint.constraint_structure == target_fp.constraint_structure, "constraint structures differ"),
            (seed.fingerprint.hardware == target_fp.hardware, "hardware differs"),
        )
        for matches, reason in checks:
            if not matches:
                return SeedCompatibility(False, reason, self.name)
        if target_xdescriptions is None:
            return SeedCompatibility(False, "target ordered Xdescriptions are unavailable", self.name)
        if seed.xdescriptions != tuple(target_xdescriptions):
            return SeedCompatibility(False, "ordered Xdescriptions differ", self.name)
        return SeedCompatibility(True, "same-shape structure and ordered Xdescriptions match", self.name)


class SinglePhaseJourneyConverter(ExactTransferConverter):
    """Validated wrapper around EMTG's multi- to single-phase options converter."""

    name = "single_phase_journeys"

    def compatibility(self, seed, target, target_xdescriptions=None):
        options_path = Path(str(seed.metadata.get("options_path", "")))
        target_fp = SeedFingerprint.from_phenotype(target)
        if not options_path.is_file():
            return SeedCompatibility(False, "source options artifact is unavailable", self.name)
        if not any(count > 1 for count in seed.fingerprint.phase_counts):
            return SeedCompatibility(False, "source has no multi-phase journey", self.name)
        if any(count != 1 for count in target_fp.phase_counts):
            return SeedCompatibility(False, "target journeys are not all single-phase", self.name)
        if target_fp.journey_count != sum(seed.fingerprint.phase_counts):
            return SeedCompatibility(False, "target journey count does not match source phase count", self.name)
        expected_endpoints = []
        flyby_offset = 0
        for (departure, arrival), phase_count in zip(
            seed.fingerprint.endpoints, seed.fingerprint.phase_counts
        ):
            intermediates = seed.fingerprint.flybys[flyby_offset:flyby_offset + phase_count - 1]
            flyby_offset += max(0, phase_count - 1)
            sequence = (departure, *intermediates, arrival)
            expected_endpoints.extend(zip(sequence, sequence[1:]))
        if tuple(expected_endpoints) != target_fp.endpoints:
            return SeedCompatibility(False, "target endpoints do not match split source phases", self.name)
        if seed.fingerprint.hardware != target_fp.hardware:
            return SeedCompatibility(False, "hardware differs", self.name)
        return SeedCompatibility(True, "single-phase conversion preconditions match", self.name)

    def convert(self, seed, target, target_xdescriptions=None):
        compatibility = self.compatibility(seed, target, target_xdescriptions)
        if not compatibility.compatible:
            raise ValueError(compatibility.reason)
        try:
            from ..MissionOptions import MissionOptions
            from ..Converters.convert_to_single_phase_journeys import convert_to_single_phase_journeys
        except ImportError:
            from MissionOptions import MissionOptions
            from Converters.convert_to_single_phase_journeys import convert_to_single_phase_journeys
        converted = convert_to_single_phase_journeys(
            MissionOptions(str(seed.metadata["options_path"]))
        )
        descriptions = tuple(str(entry[0]) for entry in converted.trialX)
        vector = tuple(float(entry[1]) for entry in converted.trialX)
        if target_xdescriptions is not None and descriptions != tuple(target_xdescriptions):
            raise ValueError("converted ordered Xdescriptions do not match the target")
        return SeedArtifact.create(
            seed.source, target, descriptions, vector, seed.feasible,
            objective=seed.objective, fidelity=seed.fidelity, family=seed.family,
            metadata={**dict(seed.metadata), "converted_by": self.name},
        )


class TPSLTToPSFBConverter(ExactTransferConverter):
    """Validated MGALT/FBLT-to-PSFB wrapper using the existing EMTG utility."""

    name = "tpslt_to_psfb"

    def compatibility(self, seed, target, target_xdescriptions=None):
        options_path = Path(str(seed.metadata.get("options_path", "")))
        mission_path = Path(str(seed.metadata.get("mission_path", seed.source)))
        target_fp = SeedFingerprint.from_phenotype(target)
        if not options_path.is_file() or not mission_path.is_file():
            return SeedCompatibility(False, "source mission/options artifacts are unavailable", self.name)
        source_types = {int(value) for value in seed.fingerprint.phase_types if isinstance(value, (int, float))}
        target_types = {int(value) for value in target_fp.phase_types if isinstance(value, (int, float))}
        if not source_types or not source_types <= {2, 3}:
            return SeedCompatibility(False, "source transcription is not MGALT/FBLT", self.name)
        if target_types != {5}:
            return SeedCompatibility(False, "target transcription is not PSFB", self.name)
        if any(count != 1 for count in seed.fingerprint.phase_counts) or any(count != 1 for count in target_fp.phase_counts):
            return SeedCompatibility(False, "MGALT/FBLT-to-PSFB requires single-phase journeys", self.name)
        if seed.fingerprint.endpoints != target_fp.endpoints or seed.fingerprint.hardware != target_fp.hardware:
            return SeedCompatibility(False, "endpoint or hardware structure differs", self.name)
        return SeedCompatibility(True, "TPSLT-to-PSFB conversion preconditions match", self.name)

    def convert(self, seed, target, target_xdescriptions=None):
        compatibility = self.compatibility(seed, target, target_xdescriptions)
        if not compatibility.compatible:
            raise ValueError(compatibility.reason)
        source_type = int(next(value for value in seed.fingerprint.phase_types if isinstance(value, (int, float))))
        label = "MGALT" if source_type == 2 else "FBLT"
        try:
            from ..Converters.Convert_TwoPointShootingLowThrust_to_PSFB import MissionConverter_TPSLT_to_PSFB
        except ImportError:
            from Converters.Convert_TwoPointShootingLowThrust_to_PSFB import MissionConverter_TPSLT_to_PSFB
        converter = MissionConverter_TPSLT_to_PSFB(
            str(seed.metadata.get("mission_path", seed.source)),
            str(seed.metadata["options_path"]),
            [label, source_type],
        )
        converter.CreateMission()
        converted = converter.CreateInitialGuess()
        descriptions = tuple(str(entry[0]) for entry in converted)
        vector = tuple(float(entry[1]) for entry in converted)
        if target_xdescriptions is not None and descriptions != tuple(target_xdescriptions):
            raise ValueError("converted ordered Xdescriptions do not match the target")
        return SeedArtifact.create(
            seed.source, target, descriptions, vector, seed.feasible,
            objective=seed.objective, fidelity=seed.fidelity, family=seed.family,
            metadata={**dict(seed.metadata), "converted_by": self.name},
        )


class SeedConverterRegistry:
    def __init__(self, converters: Iterable[Any] = ()):
        self._converters: dict[str, Any] = {}
        for converter in converters:
            self.register(converter)

    def register(self, converter: Any) -> None:
        if not getattr(converter, "name", None) or converter.name in self._converters:
            raise ValueError("seed converter requires a unique name")
        self._converters[converter.name] = converter

    def get(self, name: str) -> Any:
        try:
            return self._converters[name]
        except KeyError as error:
            raise KeyError(f"unregistered seed converter: {name}") from error


def default_converter_registry() -> SeedConverterRegistry:
    return SeedConverterRegistry((
        ExactTransferConverter(), SameShapeBodySubstitutionConverter(),
        SinglePhaseJourneyConverter(), TPSLTToPSFBConverter(),
    ))


def direct_compatibility(
    seed: SeedArtifact,
    target: MissionPhenotype,
    target_xdescriptions: Sequence[str] | None,
) -> SeedCompatibility:
    target_fingerprint = SeedFingerprint.from_phenotype(target)
    if seed.fingerprint.journey_count != target_fingerprint.journey_count:
        return SeedCompatibility(False, "journey counts differ")
    if len(seed.fingerprint.flybys) != len(target_fingerprint.flybys):
        return SeedCompatibility(False, "flyby/phase counts differ")
    if seed.fingerprint.phase_types != target_fingerprint.phase_types:
        return SeedCompatibility(False, "phase types differ")
    if target_xdescriptions is None:
        return SeedCompatibility(False, "target decision-vector descriptions are unavailable")
    if tuple(target_xdescriptions) != seed.xdescriptions:
        return SeedCompatibility(False, "ordered decision-vector descriptions differ")
    return SeedCompatibility(True, "structural signature and decision-vector descriptions match")


def _lcs_length(left: Sequence[str], right: Sequence[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_value in left:
        current = [0]
        for index, right_value in enumerate(right, 1):
            current.append(previous[index - 1] + 1 if left_value == right_value else max(previous[index], current[-1]))
        previous = current
    return previous[-1]


def fingerprint_distance(
    left: SeedFingerprint,
    right: SeedFingerprint,
    weights: Mapping[str, float] | None = None,
) -> float:
    weight = {
        "endpoints": 5.0,
        "flybys": 3.0,
        "journeys": 4.0,
        "phase_types": 2.0,
        "launch_epoch": 0.01,
        "flight_time": 0.01,
        "hardware": 2.0,
        **dict(weights or {}),
    }
    endpoint_mismatches = sum(a != b for a, b in zip(left.endpoints, right.endpoints)) + abs(len(left.endpoints) - len(right.endpoints))
    lcs = _lcs_length(left.flybys, right.flybys)
    flyby_distance = len(left.flybys) + len(right.flybys) - 2 * lcs
    phase_mismatches = sum(a != b for a, b in zip(left.phase_types, right.phase_types)) + abs(len(left.phase_types) - len(right.phase_types))
    epoch_distance = abs(left.launch_epoch - right.launch_epoch) if left.launch_epoch is not None and right.launch_epoch is not None else 0.0
    time_distance = abs(left.flight_time - right.flight_time) if left.flight_time is not None and right.flight_time is not None else 0.0
    hardware_distance = len(set(left.hardware).symmetric_difference(right.hardware))
    return (
        weight["endpoints"] * endpoint_mismatches
        + weight["flybys"] * flyby_distance
        + weight["journeys"] * abs(left.journey_count - right.journey_count)
        + weight["phase_types"] * phase_mismatches
        + weight["launch_epoch"] * epoch_distance
        + weight["flight_time"] * time_distance
        + weight["hardware"] * hardware_distance
    )


class SeedInventory:
    def __init__(self, seeds: Iterable[SeedArtifact] = ()):
        self._seeds = {seed.seed_id: seed for seed in seeds}

    def add(self, seed: SeedArtifact) -> None:
        self._seeds[seed.seed_id] = seed

    @classmethod
    def discover(cls, folders: Iterable[str | Path]) -> tuple["SeedInventory", tuple[Mapping[str, Any], ...]]:
        """Discover schema-3 inventories and modern JSONL campaign exports."""
        inventory = cls()
        considered: list[Mapping[str, Any]] = []
        for folder_value in folders:
            folder = Path(folder_value).resolve()
            if not folder.is_dir():
                considered.append({"source": str(folder), "accepted": False, "reason": "folder does not exist"})
                continue
            for path in sorted(folder.rglob("*")):
                if not path.is_file() or path.suffix.lower() not in {".json", ".jsonl"}:
                    continue
                try:
                    if path.suffix.lower() == ".json":
                        loaded = cls.from_file(path)
                        for seed in loaded.values():
                            inventory.add(seed)
                        considered.append({"source": str(path), "accepted": True, "count": len(loaded.values())})
                        continue
                    from .serde import candidate_from_dict
                    accepted = 0
                    for line in path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        record = json.loads(line)
                        candidate_data, result = record.get("candidate"), record.get("result")
                        if not candidate_data or not result:
                            continue
                        metrics = result.get("metrics", {})
                        descriptions = metrics.get("xdescriptions")
                        vector = metrics.get("decision_vector")
                        if not descriptions or not vector or len(descriptions) != len(vector):
                            continue
                        candidate = candidate_from_dict(candidate_data)
                        inventory.add(SeedArtifact.create(
                            str(path), candidate.phenotype, descriptions, vector,
                            result.get("status") == "feasible",
                            fidelity=str(result.get("fidelity", "full")),
                            family=candidate.candidate_id,
                            metadata={"discovered_from": str(path)},
                        ))
                        accepted += 1
                    considered.append({"source": str(path), "accepted": accepted > 0, "count": accepted,
                                       "reason": None if accepted else "no target-native decision vectors"})
                except Exception as error:
                    considered.append({"source": str(path), "accepted": False, "reason": str(error)})
        return inventory, tuple(considered)

    @classmethod
    def from_file(cls, path: str | Path) -> "SeedInventory":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema_version") != 3 or not isinstance(data.get("seeds"), list):
            raise ValueError("seed inventory schema is unsupported; choose a fresh schema-3 inventory")
        seeds = []
        for value in data["seeds"]:
            fingerprint_data = value["fingerprint"]
            fingerprint = SeedFingerprint(
                endpoints=tuple(tuple(pair) for pair in fingerprint_data.get("endpoints", ())),
                flybys=tuple(fingerprint_data.get("flybys", ())),
                journey_count=int(fingerprint_data["journey_count"]),
                phase_types=tuple(fingerprint_data.get("phase_types", ())),
                launch_epoch=_optional_float(fingerprint_data.get("launch_epoch")),
                flight_time=_optional_float(fingerprint_data.get("flight_time")),
                hardware=tuple(tuple(pair) for pair in fingerprint_data.get("hardware", ())),
                phase_counts=tuple(fingerprint_data.get("phase_counts", ())),
                dsm_counts=tuple(tuple(row) for row in fingerprint_data.get("dsm_counts", ())),
                boundary_types=tuple(tuple(row) for row in fingerprint_data.get("boundary_types", ())),
                constraint_structure=tuple(tuple(row) for row in fingerprint_data.get("constraint_structure", ())),
            )
            seeds.append(
                SeedArtifact(
                    seed_id=str(value["seed_id"]),
                    source=str(value["source"]),
                    fingerprint=fingerprint,
                    xdescriptions=tuple(value.get("xdescriptions", ())),
                    decision_vector=tuple(float(item) for item in value.get("decision_vector", ())),
                    feasible=bool(value.get("feasible", False)),
                    objective=_optional_float(value.get("objective")),
                    fidelity=str(value.get("fidelity", "full")),
                    family=value.get("family"),
                    metadata=dict(value.get("metadata", {})),
                )
            )
        return cls(seeds)

    def values(self) -> tuple[SeedArtifact, ...]:
        return tuple(sorted(self._seeds.values(), key=lambda value: value.seed_id))

    def select(
        self,
        target: MissionPhenotype,
        *,
        count: int = 1,
        include_infeasible: bool = False,
        weights: Mapping[str, float] | None = None,
        family: str | None = None,
    ) -> tuple[SeedArtifact, ...]:
        fingerprint = SeedFingerprint.from_phenotype(target)
        seeds = [seed for seed in self._seeds.values() if include_infeasible or seed.feasible]
        seeds.sort(
            key=lambda seed: (
                0 if family is not None and seed.family == family else 1,
                fingerprint_distance(seed.fingerprint, fingerprint, weights),
                math_objective(seed.objective),
                seed.seed_id,
            )
        )
        return tuple(seeds[:count])

    def to_file(self, path: str | Path) -> None:
        data = []
        for seed in sorted(self._seeds.values(), key=lambda value: value.seed_id):
            data.append(
                {
                    "seed_id": seed.seed_id,
                    "source": seed.source,
                    "fingerprint": {
                        **seed.fingerprint.__dict__,
                        "endpoints": list(seed.fingerprint.endpoints),
                        "hardware": list(seed.fingerprint.hardware),
                    },
                    "xdescriptions": list(seed.xdescriptions),
                    "decision_vector": list(seed.decision_vector),
                    "feasible": seed.feasible,
                    "objective": seed.objective,
                    "fidelity": seed.fidelity,
                    "family": seed.family,
                    "metadata": dict(seed.metadata),
                }
            )
        Path(path).write_text(json.dumps({"schema_version": 3, "seeds": data}, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def math_objective(value: float | None) -> float:
    return float("inf") if value is None else value


class ExternalSeedProvider:
    """Safe JSON-file protocol for R-FFS/Lambert/user seed generators."""

    def __init__(
        self, command: Sequence[str], timeout_seconds: float = 300.0,
        environment: Mapping[str, str] | None = None,
    ):
        if not command:
            raise ValueError("external seed command is empty")
        self.command = tuple(str(value) for value in command)
        self.timeout_seconds = timeout_seconds
        self.environment = {str(key): str(value) for key, value in (environment or {}).items()}

    def identity(self) -> Mapping[str, Any]:
        executable = Path(self.command[0]).resolve()
        return {
            "command": self.command,
            "executable": str(executable),
            "executable_sha256": file_sha256(executable) if executable.is_file() else None,
            "command_file_hashes": {
                str(Path(value).resolve()): file_sha256(Path(value).resolve())
                for value in self.command
                if Path(value).expanduser().is_file()
            },
            "protocol_version": 3,
            "environment": self.environment,
        }

    def generate(
        self, request: Mapping[str, Any], work_directory: str | Path,
        cancel_event: threading.Event | None = None,
    ) -> Mapping[str, Any]:
        work = Path(work_directory).resolve()
        work.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="seed-provider-", dir=work) as temporary:
            request_path = Path(temporary) / "request.json"
            response_path = Path(temporary) / "response.json"
            request_path.write_text(json.dumps(request, sort_keys=True, allow_nan=False), encoding="utf-8")
            outcome = run_process(
                [*self.command, str(request_path), str(response_path)],
                cwd=temporary,
                timeout_seconds=self.timeout_seconds,
                stdout_path=Path(temporary) / "stdout.log",
                stderr_path=Path(temporary) / "stderr.log",
                cancel_event=cancel_event,
                environment=self.environment,
            )
            if outcome.cancelled:
                raise RuntimeError("seed provider cancelled")
            if outcome.timed_out:
                raise TimeoutError("seed provider timed out")
            if outcome.returncode != 0:
                raise RuntimeError(f"seed provider failed ({outcome.returncode}): {outcome.stderr_tail[-2000:]}")
            if not response_path.is_file():
                raise RuntimeError("seed provider did not write its response")
            response = json.loads(response_path.read_text(encoding="utf-8"))
            if not isinstance(response, Mapping):
                raise RuntimeError("seed provider response is not an object")
            return response
