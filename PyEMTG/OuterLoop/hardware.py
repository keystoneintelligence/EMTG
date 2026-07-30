"""Safe hardware-library name catalogs used before case generation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from types import MappingProxyType
from typing import Iterable, Mapping

from .canonical import file_sha256


class HardwareInspectionError(ValueError):
    """Raised when an EMTG hardware artifact cannot be interpreted safely."""


def _tokens(line: str) -> list[str]:
    return [value for value in re.split(r"[\s,]+", line.strip()) if value]


def _decimal(value: str, *, where: str) -> Decimal:
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise HardwareInspectionError(f"{where} is not numeric: {value!r}") from error
    if not result.is_finite():
        raise HardwareInspectionError(f"{where} must be finite")
    return result.normalize() if result != 0 else Decimal(0)


def _integer(value: str, *, where: str) -> int:
    number = _decimal(value, where=where)
    if number != number.to_integral_value():
        raise HardwareInspectionError(f"{where} must be an integer")
    return int(number)


def _data_lines(path: Path) -> Iterable[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise HardwareInspectionError(f"cannot read hardware artifact {path}: {error}") from error
    for number, raw in enumerate(lines, 1):
        line = raw.strip().lstrip("\ufeff")
        if line and not line.startswith("#"):
            yield number, line


def _unique_records(records: Iterable[object], path: Path) -> dict[str, object]:
    output: dict[str, object] = {}
    for record in records:
        name = str(getattr(record, "name"))
        if name in output:
            raise HardwareInspectionError(f"duplicate hardware key {name!r} in {path.name}")
        output[name] = record
    if not output:
        raise HardwareInspectionError(f"hardware library {path.name} contains no records")
    return output


@dataclass(frozen=True)
class LaunchVehicleRecord:
    name: str
    model_type: int
    dla_lower_degrees: Decimal
    dla_upper_degrees: Decimal
    c3_lower_km2_s2: Decimal
    c3_upper_km2_s2: Decimal
    adapter_mass_kg: Decimal
    coefficients: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.model_type != 0:
            raise HardwareInspectionError(
                f"launch vehicle {self.name!r} uses unsupported model type {self.model_type}"
            )
        if self.dla_lower_degrees > self.dla_upper_degrees:
            raise HardwareInspectionError(f"launch vehicle {self.name!r} has reversed DLA bounds")
        if self.c3_lower_km2_s2 < 0 or self.c3_lower_km2_s2 > self.c3_upper_km2_s2:
            raise HardwareInspectionError(f"launch vehicle {self.name!r} has invalid C3 bounds")
        if self.adapter_mass_kg < 0:
            raise HardwareInspectionError(f"launch vehicle {self.name!r} has negative adapter mass")
        if not self.coefficients:
            raise HardwareInspectionError(f"launch vehicle {self.name!r} has no performance coefficients")

    def delivered_mass_kg(self, c3_km2_s2: Decimal | float | int | str,
                          margin: Decimal | float | int | str = 0) -> Decimal:
        c3 = _decimal(str(c3_km2_s2), where="C3")
        margin_value = _decimal(str(margin), where="launch vehicle margin")
        if c3 < self.c3_lower_km2_s2 or c3 > self.c3_upper_km2_s2:
            raise HardwareInspectionError(
                f"C3 {c3} is outside {self.name} bounds "
                f"[{self.c3_lower_km2_s2}, {self.c3_upper_km2_s2}]"
            )
        if margin_value < 0 or margin_value > 1:
            raise HardwareInspectionError("launch vehicle margin must be within [0, 1]")
        total = Decimal(0)
        power = Decimal(1)
        for coefficient in self.coefficients:
            total += coefficient * power
            power *= c3
        return total * (Decimal(1) - margin_value) - self.adapter_mass_kg


@dataclass(frozen=True)
class LaunchVehicleLibrary:
    path: Path
    sha256: str
    records: Mapping[str, LaunchVehicleRecord]

    @classmethod
    def from_file(cls, path: str | Path) -> "LaunchVehicleLibrary":
        source = Path(path).resolve()
        records: list[LaunchVehicleRecord] = []
        for line_number, line in _data_lines(source):
            values = _tokens(line)
            if len(values) < 8:
                raise HardwareInspectionError(
                    f"{source.name}:{line_number} launch-vehicle row requires at least 8 tokens"
                )
            records.append(LaunchVehicleRecord(
                values[0],
                _integer(values[1], where=f"{source.name}:{line_number} model type"),
                _decimal(values[2], where=f"{source.name}:{line_number} DLA lower"),
                _decimal(values[3], where=f"{source.name}:{line_number} DLA upper"),
                _decimal(values[4], where=f"{source.name}:{line_number} C3 lower"),
                _decimal(values[5], where=f"{source.name}:{line_number} C3 upper"),
                _decimal(values[6], where=f"{source.name}:{line_number} adapter mass"),
                tuple(_decimal(value, where=f"{source.name}:{line_number} coefficient")
                      for value in values[7:]),
            ))
        unique = _unique_records(records, source)
        return cls(source, file_sha256(source), MappingProxyType(unique))  # type: ignore[arg-type]

    def get(self, name: str) -> LaunchVehicleRecord:
        try:
            return self.records[name]
        except KeyError as error:
            raise HardwareInspectionError(
                f"launch vehicle {name!r} does not exist in {self.path.name}"
            ) from error


@dataclass(frozen=True)
class PowerSystemRecord:
    name: str
    supply_type: int
    supply_curve_type: int
    bus_power_type: int
    p0_kw: Decimal
    mass_per_kw_kg: Decimal
    decay_rate: Decimal
    gamma: tuple[Decimal, ...]
    bus_coefficients: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.supply_type not in {0, 1} or self.supply_curve_type not in {0, 1} or self.bus_power_type not in {0, 1}:
            raise HardwareInspectionError(f"power system {self.name!r} has an unknown model enum")
        if len(self.gamma) != 7 or len(self.bus_coefficients) != 3:
            raise HardwareInspectionError(f"power system {self.name!r} has malformed coefficients")
        if self.p0_kw <= 0:
            raise HardwareInspectionError(f"power system {self.name!r} must have positive P0")

    def identity(self) -> tuple[object, ...]:
        return (self.name, self.supply_type, self.supply_curve_type, self.bus_power_type,
                self.p0_kw, self.mass_per_kw_kg, self.decay_rate, self.gamma, self.bus_coefficients)


@dataclass(frozen=True)
class PowerSystemLibrary:
    path: Path
    sha256: str
    records: Mapping[str, PowerSystemRecord]

    @classmethod
    def from_file(cls, path: str | Path) -> "PowerSystemLibrary":
        source = Path(path).resolve()
        records: list[PowerSystemRecord] = []
        for line_number, line in _data_lines(source):
            values = _tokens(line)
            if len(values) < 17:
                raise HardwareInspectionError(
                    f"{source.name}:{line_number} power-system row requires at least 17 tokens"
                )
            records.append(PowerSystemRecord(
                values[0],
                _integer(values[1], where=f"{source.name}:{line_number} supply type"),
                _integer(values[2], where=f"{source.name}:{line_number} curve type"),
                _integer(values[3], where=f"{source.name}:{line_number} bus type"),
                _decimal(values[4], where=f"{source.name}:{line_number} P0"),
                _decimal(values[5], where=f"{source.name}:{line_number} mass/kW"),
                _decimal(values[6], where=f"{source.name}:{line_number} decay"),
                tuple(_decimal(value, where=f"{source.name}:{line_number} gamma")
                      for value in values[7:14]),
                tuple(_decimal(value, where=f"{source.name}:{line_number} bus coefficient")
                      for value in values[14:17]),
            ))
        unique = _unique_records(records, source)
        return cls(source, file_sha256(source), MappingProxyType(unique))  # type: ignore[arg-type]

    def get(self, name: str) -> PowerSystemRecord:
        try:
            return self.records[name]
        except KeyError as error:
            raise HardwareInspectionError(
                f"power system {name!r} does not exist in {self.path.name}"
            ) from error


@dataclass(frozen=True)
class PropulsionSystemRecord:
    name: str
    thruster_mode: int
    throttle_table_file: str
    mass_per_string_kg: Decimal
    number_of_strings: int
    pmin_kw: Decimal
    pmax_kw: Decimal
    constant_thrust_n: Decimal
    constant_isp_s: Decimal
    minimum_or_monoprop_isp_s: Decimal
    fixed_efficiency: Decimal
    mixture_ratio: Decimal
    thrust_scale: Decimal
    thrust_coefficients: tuple[Decimal, ...]
    mass_flow_coefficients: tuple[Decimal, ...]

    def __post_init__(self) -> None:
        if self.thruster_mode not in set(range(11)):
            raise HardwareInspectionError(
                f"propulsion system {self.name!r} has unknown thruster mode {self.thruster_mode}"
            )
        if self.number_of_strings < 1:
            raise HardwareInspectionError(f"propulsion system {self.name!r} has invalid engine count")
        if self.pmin_kw < 0 or self.pmin_kw > self.pmax_kw:
            raise HardwareInspectionError(f"propulsion system {self.name!r} has invalid power bounds")
        if len(self.thrust_coefficients) != 7 or len(self.mass_flow_coefficients) != 7:
            raise HardwareInspectionError(f"propulsion system {self.name!r} has malformed coefficients")

    def identity(self) -> tuple[object, ...]:
        return (
            self.name, self.thruster_mode, self.throttle_table_file, self.mass_per_string_kg,
            self.number_of_strings, self.pmin_kw, self.pmax_kw, self.constant_thrust_n,
            self.constant_isp_s, self.minimum_or_monoprop_isp_s, self.fixed_efficiency,
            self.mixture_ratio, self.thrust_scale, self.thrust_coefficients,
            self.mass_flow_coefficients,
        )


@dataclass(frozen=True)
class PropulsionSystemLibrary:
    path: Path
    sha256: str
    records: Mapping[str, PropulsionSystemRecord]

    @classmethod
    def from_file(cls, path: str | Path) -> "PropulsionSystemLibrary":
        source = Path(path).resolve()
        records: list[PropulsionSystemRecord] = []
        for line_number, line in _data_lines(source):
            values = _tokens(line)
            if len(values) < 27:
                raise HardwareInspectionError(
                    f"{source.name}:{line_number} propulsion-system row requires at least 27 tokens"
                )
            records.append(PropulsionSystemRecord(
                values[0],
                _integer(values[1], where=f"{source.name}:{line_number} thruster mode"),
                values[2],
                _decimal(values[3], where=f"{source.name}:{line_number} mass/string"),
                _integer(values[4], where=f"{source.name}:{line_number} number of strings"),
                _decimal(values[5], where=f"{source.name}:{line_number} Pmin"),
                _decimal(values[6], where=f"{source.name}:{line_number} Pmax"),
                _decimal(values[7], where=f"{source.name}:{line_number} constant thrust"),
                _decimal(values[8], where=f"{source.name}:{line_number} constant Isp"),
                _decimal(values[9], where=f"{source.name}:{line_number} minimum Isp"),
                _decimal(values[10], where=f"{source.name}:{line_number} efficiency"),
                _decimal(values[11], where=f"{source.name}:{line_number} mixture ratio"),
                _decimal(values[12], where=f"{source.name}:{line_number} thrust scale"),
                tuple(_decimal(value, where=f"{source.name}:{line_number} thrust coefficient")
                      for value in values[13:20]),
                tuple(_decimal(value, where=f"{source.name}:{line_number} mass-flow coefficient")
                      for value in values[20:27]),
            ))
        unique = _unique_records(records, source)
        return cls(source, file_sha256(source), MappingProxyType(unique))  # type: ignore[arg-type]

    def get(self, name: str) -> PropulsionSystemRecord:
        try:
            return self.records[name]
        except KeyError as error:
            raise HardwareInspectionError(
                f"propulsion system {name!r} does not exist in {self.path.name}"
            ) from error


@dataclass(frozen=True)
class ThrottleSettingRecord:
    key: str
    mass_flow_mg_s: Decimal
    beam_current_a: Decimal
    beam_voltage_v: Decimal
    thrust_mn: Decimal
    isp_s: Decimal
    efficiency: Decimal
    input_power_kw: Decimal

    def __post_init__(self) -> None:
        if re.fullmatch(r"(?:[0-9]+x)?TL[0-9]+", self.key) is None:
            raise HardwareInspectionError(f"invalid throttle-setting key {self.key!r}")
        if self.mass_flow_mg_s < 0 or self.thrust_mn < 0 or self.isp_s <= 0 or self.input_power_kw < 0:
            raise HardwareInspectionError(f"throttle setting {self.key!r} has invalid performance values")


@dataclass(frozen=True)
class ThrottleTableInspection:
    path: Path
    sha256: str
    ppu_efficiency: Decimal | None
    ppu_min_power_kw: Decimal | None
    ppu_max_power_kw: Decimal | None
    settings: Mapping[str, ThrottleSettingRecord]
    polynomial_rows: Mapping[str, tuple[Decimal, ...]]

    @classmethod
    def from_file(cls, path: str | Path) -> "ThrottleTableInspection":
        source = Path(path).resolve()
        metadata: dict[str, Decimal] = {}
        settings: dict[str, ThrottleSettingRecord] = {}
        polynomials: dict[str, tuple[Decimal, ...]] = {}
        metadata_keys = {
            "PPU efficiency": "ppu_efficiency",
            "PPU min power (kW)": "ppu_min_power_kw",
            "PPU max power (kW)": "ppu_max_power_kw",
            "power smoothing radius (kW)": "power_smoothing_radius_kw",
            "voltage smoothing radius (V)": "voltage_smoothing_radius_v",
            "Mdot smoothing radius (mg/s)": "mdot_smoothing_radius_mg_s",
        }
        polynomial_keys = {
            "high_thrust_Thrust", "high_thrust_Mdot",
            "high_Isp_Thrust", "high_Isp_Mdot",
            "2Dpolyrow1", "2Dpolyrow2", "2Dpolyrow3", "2Dpolyrow4", "2Dpolyrow5",
        }
        for line_number, line in _data_lines(source):
            values = [value.strip() for value in line.split(",")]
            key = values[0]
            if key == "Throttle level":
                if len(values) != 8:
                    raise HardwareInspectionError(
                        f"{source.name}:{line_number} throttle-table header requires 8 columns"
                    )
                continue
            if key in metadata_keys:
                normalized_key = metadata_keys[key]
                if len(values) != 2:
                    raise HardwareInspectionError(
                        f"{source.name}:{line_number} {key} requires one value"
                    )
                if normalized_key in metadata:
                    raise HardwareInspectionError(
                        f"duplicate throttle-table key {key!r} in {source.name}"
                    )
                metadata[normalized_key] = _decimal(
                    values[1], where=f"{source.name}:{line_number} {key}"
                )
                continue
            if key in polynomial_keys:
                if len(values) != 6:
                    raise HardwareInspectionError(
                        f"{source.name}:{line_number} polynomial row requires 5 coefficients"
                    )
                if key in polynomials:
                    raise HardwareInspectionError(
                        f"duplicate throttle-table key {key!r} in {source.name}"
                    )
                polynomials[key] = tuple(
                    _decimal(value, where=f"{source.name}:{line_number} {key}")
                    for value in values[1:]
                )
                continue
            if len(values) != 8:
                raise HardwareInspectionError(
                    f"{source.name}:{line_number} throttle setting requires 8 columns"
                )
            if key in settings:
                raise HardwareInspectionError(
                    f"duplicate throttle setting {key!r} in {source.name}"
                )
            numbers = tuple(
                _decimal(value, where=f"{source.name}:{line_number} {key}")
                for value in values[1:]
            )
            settings[key] = ThrottleSettingRecord(key, *numbers)
        if not settings and not polynomials:
            raise HardwareInspectionError(f"throttle table {source.name} contains no model records")
        minimum = metadata.get("ppu_min_power_kw")
        maximum = metadata.get("ppu_max_power_kw")
        efficiency = metadata.get("ppu_efficiency")
        if efficiency is not None and efficiency <= 0:
            raise HardwareInspectionError("PPU efficiency must be positive")
        if minimum is not None and maximum is not None and (minimum < 0 or minimum > maximum):
            raise HardwareInspectionError("PPU power bounds are invalid")
        return cls(
            source, file_sha256(source), efficiency, minimum, maximum,
            MappingProxyType(settings), MappingProxyType(polynomials),
        )

    def validate_for_mode(self, thruster_mode: int) -> None:
        if thruster_mode in {4, 5, 6, 7, 8, 9} and not self.settings:
            raise HardwareInspectionError(
                f"thruster mode {thruster_mode} requires throttle settings in {self.path.name}"
            )
        if thruster_mode == 10:
            expected = {f"2Dpolyrow{index}" for index in range(1, 6)}
            missing = expected - set(self.polynomial_rows)
            if missing:
                raise HardwareInspectionError(
                    f"thruster mode 10 is missing polynomial rows {sorted(missing)}"
                )


@dataclass(frozen=True)
class SpacecraftStageInspection:
    name: str
    power_system_key: str
    electric_propulsion_key: str
    chemical_propulsion_key: str
    power_systems: Mapping[str, PowerSystemRecord]
    propulsion_systems: Mapping[str, PropulsionSystemRecord]

    @property
    def effective_power_system(self) -> PowerSystemRecord:
        try:
            return self.power_systems[self.power_system_key]
        except KeyError as error:
            raise HardwareInspectionError(
                f"stage {self.name!r} references missing power system {self.power_system_key!r}"
            ) from error

    @property
    def effective_electric_propulsion(self) -> PropulsionSystemRecord:
        try:
            return self.propulsion_systems[self.electric_propulsion_key]
        except KeyError as error:
            raise HardwareInspectionError(
                f"stage {self.name!r} references missing electric propulsion system "
                f"{self.electric_propulsion_key!r}"
            ) from error


@dataclass(frozen=True)
class SpacecraftInspection:
    path: Path
    sha256: str
    name: str
    global_electric_constraint_enabled: bool
    global_electric_capacity_kg: Decimal
    stages: tuple[SpacecraftStageInspection, ...]

    @classmethod
    def from_file(cls, path: str | Path) -> "SpacecraftInspection":
        source = Path(path).resolve()
        try:
            lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            raise HardwareInspectionError(f"cannot read spacecraft artifact {source}: {error}") from error

        spacecraft_name = ""
        enabled = False
        capacity = Decimal(0)
        stages: list[SpacecraftStageInspection] = []
        seen_spacecraft_keys: set[str] = set()
        index = 0
        while index < len(lines):
            line = lines[index].strip()
            if line == "#BeginStageBlock":
                stage, index = _parse_spacecraft_stage(lines, index + 1, source)
                stages.append(stage)
                continue
            if line and not line.startswith("#"):
                values = _tokens(line)
                if values[0] == "name" and not stages:
                    if len(values) != 2:
                        raise HardwareInspectionError(f"{source.name}:{index + 1} malformed spacecraft name")
                    if values[0] in seen_spacecraft_keys:
                        raise HardwareInspectionError(f"duplicate spacecraft key {values[0]!r}")
                    seen_spacecraft_keys.add(values[0])
                    spacecraft_name = values[1]
                elif values[0] == "EnableGlobalElectricPropellantTankConstraint":
                    if len(values) != 2:
                        raise HardwareInspectionError(f"{source.name}:{index + 1} malformed tank flag")
                    if values[0] in seen_spacecraft_keys:
                        raise HardwareInspectionError(f"duplicate spacecraft key {values[0]!r}")
                    seen_spacecraft_keys.add(values[0])
                    enabled_value = _integer(values[1], where="global electric constraint")
                    if enabled_value not in {0, 1}:
                        raise HardwareInspectionError("global electric constraint must be 0 or 1")
                    enabled = bool(enabled_value)
                elif values[0] == "GlobalElectricPropellantTankCapacity":
                    if len(values) != 2:
                        raise HardwareInspectionError(f"{source.name}:{index + 1} malformed tank capacity")
                    if values[0] in seen_spacecraft_keys:
                        raise HardwareInspectionError(f"duplicate spacecraft key {values[0]!r}")
                    seen_spacecraft_keys.add(values[0])
                    capacity = _decimal(values[1], where="global electric capacity")
            index += 1
        if not stages:
            raise HardwareInspectionError(f"spacecraft file {source.name} contains no stages")
        stage_names = [stage.name for stage in stages]
        if len(set(stage_names)) != len(stage_names):
            raise HardwareInspectionError(f"spacecraft file {source.name} contains duplicate stage names")
        if capacity < 0:
            raise HardwareInspectionError("global electric propellant capacity cannot be negative")
        return cls(source, file_sha256(source), spacecraft_name, enabled, capacity, tuple(stages))

    def effective_power_systems(self) -> tuple[PowerSystemRecord, ...]:
        return tuple(stage.effective_power_system for stage in self.stages)

    def effective_electric_propulsion_systems(self) -> tuple[PropulsionSystemRecord, ...]:
        return tuple(stage.effective_electric_propulsion for stage in self.stages)


def _parse_embedded_records(
    lines: list[str], index: int, source: Path, end_marker: str, parser: str,
) -> tuple[Mapping[str, object], int]:
    records: list[object] = []
    while index < len(lines):
        line = lines[index].strip()
        if line == end_marker:
            return MappingProxyType(_unique_records(records, source)), index + 1
        if line and not line.startswith("#"):
            values = _tokens(line)
            if parser == "power":
                if len(values) < 17:
                    raise HardwareInspectionError(
                        f"{source.name}:{index + 1} embedded power row requires at least 17 tokens"
                    )
                records.append(PowerSystemRecord(
                    values[0], _integer(values[1], where="supply type"),
                    _integer(values[2], where="curve type"), _integer(values[3], where="bus type"),
                    _decimal(values[4], where="P0"), _decimal(values[5], where="mass/kW"),
                    _decimal(values[6], where="decay"),
                    tuple(_decimal(value, where="gamma") for value in values[7:14]),
                    tuple(_decimal(value, where="bus coefficient") for value in values[14:17]),
                ))
            else:
                if len(values) < 27:
                    raise HardwareInspectionError(
                        f"{source.name}:{index + 1} embedded propulsion row requires at least 27 tokens"
                    )
                records.append(PropulsionSystemRecord(
                    values[0], _integer(values[1], where="thruster mode"), values[2],
                    _decimal(values[3], where="mass/string"),
                    _integer(values[4], where="number of strings"),
                    _decimal(values[5], where="Pmin"), _decimal(values[6], where="Pmax"),
                    _decimal(values[7], where="constant thrust"),
                    _decimal(values[8], where="constant Isp"),
                    _decimal(values[9], where="minimum Isp"),
                    _decimal(values[10], where="efficiency"),
                    _decimal(values[11], where="mixture ratio"),
                    _decimal(values[12], where="thrust scale"),
                    tuple(_decimal(value, where="thrust coefficient") for value in values[13:20]),
                    tuple(_decimal(value, where="mass-flow coefficient") for value in values[20:27]),
                ))
        index += 1
    raise HardwareInspectionError(f"{source.name} is missing {end_marker}")


def _parse_spacecraft_stage(
    lines: list[str], index: int, source: Path,
) -> tuple[SpacecraftStageInspection, int]:
    name = ""
    power_key = ""
    electric_key = ""
    chemical_key = ""
    power_records: Mapping[str, PowerSystemRecord] = MappingProxyType({})
    propulsion_records: Mapping[str, PropulsionSystemRecord] = MappingProxyType({})
    seen_keys: set[str] = set()
    seen_power_block = False
    seen_propulsion_block = False
    while index < len(lines):
        line = lines[index].strip()
        if line == "#EndStageBlock":
            if not all((name, power_key, electric_key, chemical_key)):
                raise HardwareInspectionError(f"{source.name} contains an incomplete stage block")
            stage = SpacecraftStageInspection(
                name, power_key, electric_key, chemical_key, power_records, propulsion_records
            )
            # Force reference validation during discovery.
            stage.effective_power_system
            stage.effective_electric_propulsion
            if chemical_key not in propulsion_records:
                raise HardwareInspectionError(
                    f"stage {name!r} references missing chemical propulsion system {chemical_key!r}"
                )
            return stage, index + 1
        if line == "#BeginStagePowerLibraryBlock":
            if seen_power_block:
                raise HardwareInspectionError(f"{source.name} stage {name!r} repeats its power block")
            seen_power_block = True
            raw, index = _parse_embedded_records(
                lines, index + 1, source, "#EndHardwareBlock", "power"
            )
            power_records = raw  # type: ignore[assignment]
            continue
        if line == "#BeginStagePropulsionLibraryBlock":
            if seen_propulsion_block:
                raise HardwareInspectionError(
                    f"{source.name} stage {name!r} repeats its propulsion block"
                )
            seen_propulsion_block = True
            raw, index = _parse_embedded_records(
                lines, index + 1, source, "#EndHardwareBlock", "propulsion"
            )
            propulsion_records = raw  # type: ignore[assignment]
            continue
        if line and not line.startswith("#"):
            values = _tokens(line)
            if values[0] in {
                "name", "PowerSystem", "ElectricPropulsionSystem", "ChemicalPropulsionSystem",
            }:
                if len(values) != 2:
                    raise HardwareInspectionError(
                        f"{source.name}:{index + 1} stage key {values[0]!r} requires one value"
                    )
                if values[0] in seen_keys:
                    raise HardwareInspectionError(
                        f"duplicate stage key {values[0]!r} in {source.name}"
                    )
                seen_keys.add(values[0])
                if values[0] == "name":
                    name = values[1]
                elif values[0] == "PowerSystem":
                    power_key = values[1]
                elif values[0] == "ElectricPropulsionSystem":
                    electric_key = values[1]
                elif values[0] == "ChemicalPropulsionSystem":
                    chemical_key = values[1]
        index += 1
    raise HardwareInspectionError(f"{source.name} is missing #EndStageBlock")


def resolve_hardware_reference(root: str | Path, reference: str) -> Path | None:
    """Resolve EMTG's frequently absolute, installation-specific hardware paths."""
    if reference.lower() in {"", "none", "mythrottletable.throttletable"}:
        return None
    directory = Path(root).resolve()
    direct = Path(reference)
    candidates = [direct] if direct.is_absolute() else [directory / direct]
    basename = directory / direct.name
    if basename not in candidates:
        candidates.append(basename)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _library_keys(path: Path) -> tuple[str, ...]:
    keys = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as error:
        raise ValueError(f"cannot read hardware library {path}: {error}") from error
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key = line.replace(",", " ").split()[0]
        if key in keys:
            raise ValueError(f"duplicate hardware key {key} in {path.name}")
        keys.append(key)
    return tuple(keys)


@dataclass(frozen=True)
class HardwareCatalog:
    root: Path
    launch_vehicles: tuple[str, ...]
    power_systems: tuple[str, ...]
    propulsion_systems: tuple[str, ...]
    spacecraft_files: tuple[str, ...]

    @classmethod
    def from_options(cls, root: str | Path, options: object) -> "HardwareCatalog":
        directory = Path(root).resolve()
        launch = directory / str(getattr(options, "LaunchVehicleLibraryFile"))
        power = directory / str(getattr(options, "PowerSystemsLibraryFile"))
        propulsion = directory / str(getattr(options, "PropulsionSystemsLibraryFile"))
        return cls(
            directory,
            _library_keys(launch),
            _library_keys(power),
            _library_keys(propulsion),
            tuple(sorted(path.name for path in directory.glob("*.emtg_spacecraftopt"))),
        )

    def validate_choice(self, category: str, value: object) -> None:
        menus = {
            "launch_vehicle": self.launch_vehicles,
            "power_system": self.power_systems,
            "electric_propulsion_system": self.propulsion_systems,
            "chemical_propulsion_system": self.propulsion_systems,
            "spacecraft_configuration": self.spacecraft_files,
        }
        if category not in menus:
            raise KeyError(category)
        if category == "spacecraft_configuration" and isinstance(value, int):
            if value not in {0, 1, 2}:
                raise ValueError(f"SpacecraftModelInput {value} is invalid")
            return
        if str(value) not in menus[category]:
            raise ValueError(f"{category} choice {value!r} does not exist in {self.root}")
