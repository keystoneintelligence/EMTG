from __future__ import annotations

import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyEMTG.Solution import (
    CONTRACT_SCHEMA,
    EMTG_SOLUTION_KIND,
    create_solution_package,
)


def test_solution_package_is_portable_and_deterministic(tmp_path: Path):
    mission = tmp_path / "earth_mars.emtg"
    mission.write_text(
        "\n".join(
            [
                "Journey: 0",
                "Central Body: Sun",
                "Frame: ICRF",
                "J = 42.5",
                "Solution attempt that produced a feasible solution: 1",
                "Xdescriptions,a",
                "Decision Vector:,1",
                "Fdescriptions,c",
                "Constraint_Vector,0",
                "Total deterministic deltav (km/s): 3.25",
            ]
        ),
        encoding="utf-8",
    )
    options = tmp_path / "earth_mars.emtgopt"
    options.write_text("mission_name earth_mars\n", encoding="utf-8")

    first = create_solution_package(
        mission,
        tmp_path / "first.dspkg",
        artifacts={"options": options},
        metadata={
            "evaluation_key": "eval-1",
            "fidelity": "high",
            "family": {
                "family_id": "fam_" + "a" * 64,
                "sample_key": "sample-1",
            },
        },
    )
    second = create_solution_package(
        mission,
        tmp_path / "second.dspkg",
        artifacts={"options": options},
        metadata={
            "evaluation_key": "eval-1",
            "fidelity": "high",
            "family": {
                "family_id": "fam_" + "a" * 64,
                "sample_key": "sample-1",
            },
        },
    )

    with zipfile.ZipFile(first) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        summary = json.loads(archive.read("generated/solution.json"))
        trajectory = json.loads(archive.read("generated/trajectory.json"))
    with zipfile.ZipFile(second) as archive:
        second_manifest = json.loads(archive.read("manifest.json"))

    assert manifest["schema"] == CONTRACT_SCHEMA
    assert manifest["kind"] == EMTG_SOLUTION_KIND
    assert manifest["package_id"] == second_manifest["package_id"]
    assert manifest["metadata"]["family"] == {
        "family_id": "fam_" + "a" * 64,
        "sample_key": "sample-1",
    }
    assert summary["evaluation_key"] == "eval-1"
    assert summary["feasible"] is True
    assert summary["objective"] == 42.5
    assert trajectory["frame"] == "ICRF"
    assert {item["role"] for item in manifest["artifacts"]} == {
        "emtg-output",
        "options",
        "solution-summary",
        "trajectory",
    }
