from __future__ import annotations

import json
from pathlib import Path

from OuterLoop.cli import main
from OuterLoop.legacy import read_legacy_nsgaii


def configuration(tmp_path: Path) -> Path:
    source = tmp_path / "campaign.json"
    source.write_text(
        json.dumps(
            {
        "schema_version": "outerloop/v2",
                "run_directory": "run",
                "root_seed": 11,
                "search": {
                    "max_journeys": 1,
                    "max_flybys": 1,
                    "fixed_start": "Earth",
                    "fixed_final": "Mars",
                    "flyby_bodies": ["Venus"],
                },
                "objectives": ["emtg_objective", "delivered_mass"],
                "algorithm": {"population_size": 4, "generations": 1, "tournament_size": 2},
                "evaluator": {"type": "synthetic", "problem": "architecture"},
                "workers": {"count": 2},
            }
        ),
        encoding="utf-8",
    )
    return source


def test_cli_validate_run_status_inspect_and_export(tmp_path, capsys):
    source = configuration(tmp_path)
    assert main(["validate", str(source)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"]
    assert main(["run", str(source)]) == 0
    outcome = json.loads(capsys.readouterr().out)
    assert outcome["complete"]
    run = tmp_path / "run"
    assert main(["status", str(run)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["checkpoint"]["status"] == "complete"
    assert main(["export", str(run)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert Path(exported["all_jsonl"]).is_file()
    legacy = read_legacy_nsgaii(exported["legacy"])
    assert legacy.records
    import NSGAIIpopulation

    historical_reader = NSGAIIpopulation.NSGAII_outerloop_population(exported["legacy"])
    assert historical_reader.success == 1
    assert historical_reader.solutions
    first = json.loads(Path(exported["all_jsonl"]).read_text(encoding="utf-8").splitlines()[0])
    candidate_id = first["candidate"]["phenotype"]["mission"]
    # Use the canonical ID from CSV because phenotype JSON intentionally does
    # not repeat a derived value.
    import csv
    with Path(exported["all_csv"]).open(newline="", encoding="utf-8") as stream:
        candidate_id = next(csv.DictReader(stream))["candidate_id"]
    assert main(["inspect", str(run), candidate_id]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["candidate"]["phenotype"]["journeys"]
