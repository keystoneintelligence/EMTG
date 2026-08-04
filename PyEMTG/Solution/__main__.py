from __future__ import annotations

import argparse
import json
from pathlib import Path

from .exporter import create_solution_package


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m PyEMTG.Solution",
        description="Export an EMTG mission output as a DeepSpace .dspkg",
    )
    parser.add_argument("mission_output", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--artifact", action="append", default=[], metavar="ROLE=PATH")
    parser.add_argument("--metadata", help="JSON object with producer provenance")
    args = parser.parse_args(argv)
    artifacts: dict[str, Path] = {}
    for value in args.artifact:
        if "=" not in value:
            parser.error("--artifact must be ROLE=PATH")
        role, path = value.split("=", 1)
        artifacts[role] = Path(path)
    metadata = json.loads(args.metadata) if args.metadata else {}
    if not isinstance(metadata, dict):
        parser.error("--metadata must contain a JSON object")
    output = create_solution_package(
        args.mission_output,
        args.output,
        artifacts=artifacts,
        metadata=metadata,
    )
    print(output)
    return 0


raise SystemExit(main())
