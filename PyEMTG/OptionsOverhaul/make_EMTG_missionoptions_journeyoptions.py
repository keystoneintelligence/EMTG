"""Generate EMTG C++ and Python option classes from the checked-in CSV schema."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import time

from optionValidator import validate
from make_journeyoptions_header import make_journeyoptions_header
from make_journeyoptions_source import make_journeyoptions_source
from make_missionoptions_header import make_missionoptions_header
from make_missionoptions_source import make_missionoptions_source
from make_journeyoptions_python import make_PyEMTG_JourneyOptions
from make_missionoptions_python import make_PyEMTG_MissionOptions


SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_REPOSITORY_ROOT = SCRIPT_PATH.parents[2]


def read_definitions(schema_file: Path) -> list[dict[str, str]]:
    definitions: list[dict[str, str]] = []
    with schema_file.open(newline="", encoding="utf-8-sig") as csv_file:
        reader = csv.reader(csv_file)
        header: list[str] | None = None
        for row in reader:
            if reader.line_num == 1:
                header = row
                continue
            if header is None:
                raise RuntimeError(f"Missing header in {schema_file}")
            option = {key: cell for key, cell in zip(header, row) if cell != ""}
            validate(option)
            definitions.append(option)
    return definitions


def generate(repository_root: Path) -> None:
    repository_root = repository_root.resolve()
    schema_root = repository_root / "OptionsOverhaul"
    journey_definitions = read_definitions(schema_root / "list_of_journeyoptions.csv")
    mission_definitions = read_definitions(schema_root / "list_of_missionoptions.csv")
    generated_timestamp = time.strftime("%c")

    # The legacy generators concatenate their path argument, so retain one
    # explicit trailing separator while keeping all machine paths out of source.
    output_root = repository_root.as_posix() + "/"
    make_journeyoptions_header(journey_definitions, generated_timestamp, path=output_root)
    make_journeyoptions_source(journey_definitions, generated_timestamp, path=output_root)
    make_missionoptions_header(mission_definitions, generated_timestamp, path=output_root)
    make_missionoptions_source(mission_definitions, generated_timestamp, path=output_root)
    make_PyEMTG_JourneyOptions(journey_definitions, generated_timestamp, path=output_root)
    make_PyEMTG_MissionOptions(mission_definitions, generated_timestamp, path=output_root)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_REPOSITORY_ROOT,
        help="EMTG repository root (defaults to the root containing this script)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    generate(parse_args().root)
