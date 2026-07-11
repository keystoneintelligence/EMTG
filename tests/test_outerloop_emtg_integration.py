from __future__ import annotations

import os
from pathlib import Path

import pytest

from OuterLoop.evaluator import EMTGEvaluator
from OuterLoop.genome import GenomeSchema, random_genotype
from OuterLoop.model import (
    CandidateRecord,
    EvaluationRequest,
    EvaluationStatus,
    JourneyPhenotype,
    MissionPhenotype,
    PhasePhenotype,
)
from OuterLoop.randomness import random_stream
from OuterLoop.config import SearchConfig


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.emtg_integration
@pytest.mark.skipif(
    os.environ.get("EMTG_RUN_OUTERLOOP_INTEGRATION") != "1",
    reason="set EMTG_RUN_OUTERLOOP_INTEGRATION=1 for the bounded real solver test",
)
def test_bounded_real_emtg_evaluation_is_typed_and_isolated(tmp_path):
    evaluator = EMTGEvaluator(
        base_case=ROOT / "testatron" / "tests" / "transcription_tests" / "MGAnDSMs_EMintercept.emtgopt",
        executable=ROOT / "bin" / "EMTGv9.exe",
        run_directory=tmp_path,
        timeout_seconds=15,
        universe_folder=ROOT / "testatron" / "universe",
        hardware_path=ROOT / "testatron" / "HardwareModels",
        brief_executable=ROOT / "depend" / "cspice" / "exe" / "brief.exe",
        ephemeris_source_override=1,
    )
    genotype = random_genotype(
        GenomeSchema(SearchConfig(max_journeys=1, fixed_start="Earth", fixed_final="Mars")),
        random_stream(1, "integration"),
    )
    phenotype = MissionPhenotype(
        {},
        (
            JourneyPhenotype(
                "Earth",
                "Mars",
                (),
                {"phase_type": 6, "arrival_type": 2},
                (PhasePhenotype("Mars", {"phase_type": 6, "dsm_count": 1}),),
            ),
        ),
    )
    candidate = CandidateRecord("real-integration", genotype, phenotype, 0)
    request = EvaluationRequest(
        candidate,
        "short",
        7,
        {
            "inner_loop": "mbh",
            "mbh_max_run_time": 5,
            "mbh_max_trials": 100,
            "nlp_solver_type": 2,
            "nlp_max_run_time": 5,
            "nlp_major_iterations": 100,
        },
        context={"evaluator": evaluator.context_identity()},
    )
    result = evaluator.evaluate(request)
    assert result.status is EvaluationStatus.FEASIBLE
    assert result.metrics["emtg_objective"] < 0.0
    assert result.metrics["flight_time"] > 0.0
    assert Path(result.artifacts["case_directory"]).is_dir()
    assert Path(result.artifacts["options"]).is_file()
    assert Path(result.artifacts["stdout"]).is_file()
    assert result.provenance["process_arguments"][0] == str((ROOT / "bin" / "EMTGv9.exe").resolve())
