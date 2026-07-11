"""Safe hardware-library name catalogs used before case generation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


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
