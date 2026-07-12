from __future__ import annotations

import argparse
import json
from pathlib import Path
import traceback

from ..MissionOptions import MissionOptions
from ..OuterLoop.process import run_process
from ..OuterLoop.reporting import promote_candidate
from .storage import StudioStore


def materialize_solution(database: str | Path, solution_id: str) -> int:
    seed = StudioStore(database, Path(database).resolve().parents[2], recover=False)
    solution = seed.solution(solution_id)
    job = seed.job(solution["job_id"])
    store = StudioStore(database, job["source_root"], recover=False)
    run_directory = Path(job["run_directory"])
    target_directory = run_directory / "studio-trajectories" / solution_id
    target_directory.mkdir(parents=True, exist_ok=True)
    try:
        promoted = promote_candidate(
            run_directory,
            solution["candidate_id"],
            target_directory / "propagation.emtgopt",
        )
        options = MissionOptions(str(promoted))
        if not options.success:
            raise ValueError("promoted propagation options could not be parsed")
        options.generate_forward_integrated_ephemeris = 1
        options.forward_integrated_ephemeris_minimum_timestep_kept = 120.0
        options.append_mass_to_ephemeris_output = 1
        options.append_control_to_ephemeris_output = 1
        options.append_thrust_to_ephemeris_output = 1
        options.append_mdot_to_ephemeris_output = 1
        options.append_Isp_to_ephemeris_output = 1
        options.append_active_power_to_ephemeris_output = 1
        options.append_number_of_active_engines_to_ephemeris_output = 1
        options.override_working_directory = 1
        options.forced_working_directory = str(target_directory).replace("\\", "/")
        options.run_inner_loop = 0
        options.write_options_file(str(promoted), True)
        resolved = json.loads((run_directory / "resolved-config.json").read_text(encoding="utf-8"))
        executable = Path(resolved["assets"]["executable"])
        outcome = run_process(
            [executable, promoted], cwd=target_directory, timeout_seconds=600.0,
            stdout_path=target_directory / "stdout.log", stderr_path=target_directory / "stderr.log",
            environment={"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"},
        )
        if outcome.timed_out:
            raise TimeoutError("propagation-only ephemeris materialization timed out")
        ephemerides = sorted(target_directory.glob("*.ephemeris"))
        if not ephemerides:
            raise RuntimeError(f"EMTG produced no ephemeris; return code {outcome.returncode}")
        selected = ephemerides[0]
        sample_count = max(0, sum(1 for _ in selected.open("r", encoding="utf-8", errors="replace")) - 1)
        store.mark_trajectory(
            solution_id, "dense", status="available", artifact_path=str(selected),
            # EMTG's forward-integrated .ephemeris columns are emitted in
            # J2000/ICRF even when the human-readable .emtg event table uses
            # the selected body-inertial output frame.
            frame="J2000/ICRF", sample_count=sample_count,
        )
        return 0
    except Exception as error:
        store.mark_trajectory(
            solution_id, "dense", status="failed",
            error=f"{error}\n{traceback.format_exc()[-8000:]}", frame="J2000",
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--solution", required=True)
    args = parser.parse_args(argv)
    return materialize_solution(args.database, args.solution)


if __name__ == "__main__":
    raise SystemExit(main())
