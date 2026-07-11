"""Safe compatibility import/export for historical .NSGAII files."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class LegacyNSGAIIRecord:
    values: Mapping[str, str]

    @property
    def description(self) -> str:
        return self.values.get("Description", "")

    @property
    def output_filename(self) -> str:
        return self.values.get("File name", "")


@dataclass(frozen=True)
class LegacyNSGAIIPopulation:
    headers: tuple[str, ...]
    gene_headers: tuple[str, ...]
    records: tuple[LegacyNSGAIIRecord, ...]


def read_legacy_nsgaii(path: str | Path) -> LegacyNSGAIIPopulation:
    source = Path(path)
    with source.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        rows = list(csv.reader(stream))
    if len(rows) < 5:
        raise ValueError("legacy NSGAII file has fewer than five header lines")
    headers = tuple(rows[3])
    genes = tuple(rows[4])
    records = []
    for row in rows[5:]:
        if not row or not any(value.strip() for value in row):
            continue
        padded = [*row, *([""] * max(0, len(headers) - len(row)))]
        records.append(LegacyNSGAIIRecord(dict(zip(headers, padded))))
    return LegacyNSGAIIPopulation(headers, genes, tuple(records))


HISTORICAL_OBJECTIVE_HEADERS = {
    "beginning_of_life_power": "BOL power at 1 AU (kW)",
    "launch_epoch": "Launch epoch (MJD)",
    "flight_time": "Flight time (days)",
    "delivered_mass": "Delivered mass to final target (kg)",
    "final_journey_mass_increment": "Final journey mass increment (for maximizing sample return)",
    "departure_c3": "First journey departure C3 (km^2/s^2)",
    "arrival_c3": "Final journey arrival C3 (km^2/s^2)",
    "arrival_declination": "Final journey arrival declination (deg)",
    "deterministic_delta_v": "Total deterministic delta-v (km/s)",
    "emtg_objective": "Inner-loop objective function",
    "point_group_value": "Point-group value",
    "total_propellant": "Total propellant mass including margin (kg)",
    "number_of_journeys": "Number of journeys",
    "dry_mass_margin": "Dry mass margin",
    "entry_interface_velocity": "Final journey interface velocity (km/s)",
    "thruster_duty_cycle": "Thruster duty cycle",
    "normalized_aggregate_control": "Normalized aggregate control",
    "bus_power": "bus power (kW)",
}


def write_legacy_nsgaii(
    path: str | Path,
    records: Iterable[Mapping[str, Any]],
    objective_names: Sequence[str],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    objective_headers = [HISTORICAL_OBJECTIVE_HEADERS.get(name, name) for name in objective_names]
    headers = ["Generation found", "File name", "timestamp", "Description", *objective_headers]
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(["# EMTG outer-loop legacy compatibility export"])
        writer.writerow(["# Modern canonical phenotype IDs are retained in Description"])
        writer.writerow(["# No hidden genotype is inferred when importing this file"])
        writer.writerow(headers)
        writer.writerow(["" for _ in headers])
        for record in records:
            metrics = record.get("metrics", {})
            writer.writerow([
                record.get("generation", 0),
                record.get("output_file", ""),
                record.get("timestamp", 0),
                record.get("description", ""),
                *(metrics.get(name, "") for name in objective_names),
            ])
