"""Run the A20136163 asteroid rendezvous integration fixture.

This is intentionally separate from testatron.py because optimizer validation is
better checked with mission-level assertions than exact truth-file comparison.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import re
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
TESTATRON_DIR = REPO_ROOT / "testatron"
DEFAULT_CASE = (
    TESTATRON_DIR
    / "tests"
    / "integration_asteroid_missions"
    / "A20136163_AEPS_IPOPT_FBLT.emtgopt"
)
DEFAULT_EMTG = REPO_ROOT / "bin" / "EMTGv9.exe"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run and validate the A20136163 AEPS/IPOPT asteroid fixture."
    )
    parser.add_argument("--emtg", default=str(DEFAULT_EMTG), help="Path to EMTGv9 executable.")
    parser.add_argument("--case", default=str(DEFAULT_CASE), help="Path to the fixture .emtgopt.")
    parser.add_argument(
        "--output-root",
        default=str(REPO_ROOT / "_local" / "integration_asteroid_missions"),
        help="Directory where timestamped run folders are created.",
    )
    parser.add_argument(
        "--validate-output-dir",
        default=None,
        help="Validate an existing output directory instead of running EMTG.",
    )
    parser.add_argument("--timeout", type=int, default=1200, help="Run timeout in seconds.")
    parser.add_argument(
        "--run-budget-seconds",
        type=int,
        default=None,
        help="Override the mission's NLP wall-clock budget in seconds.",
    )
    parser.add_argument(
        "--min-final-mass",
        type=float,
        default=1800.0,
        help="Minimum acceptable final spacecraft mass in kg.",
    )
    parser.add_argument(
        "--max-worst-constraint",
        type=float,
        default=1.0e-5,
        help="Maximum acceptable reported worst constraint violation.",
    )
    return parser.parse_args()


def load_mission_options_module() -> object:
    sys.path.insert(0, str(REPO_ROOT / "PyEMTG"))
    import MissionOptions  # type: ignore

    return MissionOptions


def write_run_options(
    case_file: Path,
    output_dir: Path,
    run_budget_seconds: int | None = None,
) -> Path:
    mission_options = load_mission_options_module()
    options = mission_options.MissionOptions(str(case_file))

    options.override_working_directory = 1
    options.forced_working_directory = output_dir.as_posix()
    options.override_mission_subfolder = 1
    options.forced_mission_subfolder = "."
    options.short_output_file_names = 1
    options.background_mode = 1
    options.universe_folder = (TESTATRON_DIR / "universe").as_posix()
    options.HardwarePath = (TESTATRON_DIR / "HardwareModels").as_posix()
    if run_budget_seconds is not None:
        if run_budget_seconds <= 0:
            raise ValueError("--run-budget-seconds must be positive")
        options.snopt_max_run_time = run_budget_seconds

    run_options = output_dir / case_file.name
    options.write_options_file(str(run_options), not options.print_only_non_default_options)
    return run_options


def read_mission_name(options_file: Path) -> str:
    for line in options_file.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[0] == "mission_name":
            return parts[1]
    raise AssertionError(f"Could not find mission_name in {options_file}")


def parse_metric(pattern: str, text: str, metric_name: str) -> float:
    match = re.search(pattern, text)
    if not match:
        raise AssertionError(f"Could not find {metric_name} in EMTG output.")
    return float(match.group(1))


def parse_feasible_flag(text: str) -> int:
    match = re.search(r"Was first NLP solve feasible:\s+(\d+)", text)
    if not match:
        raise AssertionError("Could not find first NLP feasible flag in EMTG output.")
    return int(match.group(1))


def parse_final_event(text: str) -> tuple[str, str, float]:
    event_rows = [line for line in text.splitlines() if "|" in line and "LT_rndzvs" in line]
    if not event_rows:
        raise AssertionError("Could not find an LT_rndzvs arrival event in EMTG output.")

    columns = [column.strip() for column in event_rows[-1].split("|")]
    if len(columns) < 30:
        raise AssertionError(f"Unexpected final event format: {event_rows[-1]}")

    event_type = columns[3]
    location = columns[4]
    mass = float(columns[29])
    return event_type, location, mass


def validate_output_dir(
    output_dir: Path,
    mission_name: str,
    min_final_mass: float,
    max_worst_constraint: float,
) -> None:
    mission_file = output_dir / f"{mission_name}.emtg"
    failure_file = output_dir / f"FAILURE_{mission_name}.emtg"
    ephemeris_file = output_dir / f"{mission_name}.ephemeris"

    if failure_file.exists():
        raise AssertionError(f"EMTG wrote failure output: {failure_file}")
    if not mission_file.exists():
        raise AssertionError(f"Expected mission output not found: {mission_file}")
    if not ephemeris_file.exists():
        raise AssertionError(f"Expected ephemeris output not found: {ephemeris_file}")

    text = mission_file.read_text(encoding="utf-8", errors="replace")
    feasible = parse_feasible_flag(text)
    worst_constraint = parse_metric(
        r"with violation\s+([+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)",
        text,
        "worst constraint violation",
    )
    final_mass = parse_metric(
        r"Spacecraft: Final mass including propellant margin \(kg\):\s+([+-]?\d+(?:\.\d+)?(?:[Ee][+-]?\d+)?)",
        text,
        "final mass",
    )
    event_type, location, event_mass = parse_final_event(text)

    failures = []
    if feasible != 1:
        failures.append(f"first NLP feasible flag was {feasible}")
    if abs(worst_constraint) > max_worst_constraint:
        failures.append(
            f"worst constraint {worst_constraint:.6g} exceeds {max_worst_constraint:.6g}"
        )
    if final_mass < min_final_mass:
        failures.append(f"final mass {final_mass:.3f} kg < {min_final_mass:.3f} kg")
    if event_type != "LT_rndzvs" or location != "A20136163":
        failures.append(f"final event was {event_type} at {location}, expected LT_rndzvs at A20136163")
    if abs(final_mass - event_mass) > 1.0e-2:
        failures.append(
            f"final event mass {event_mass:.6f} kg does not match summary mass {final_mass:.6f} kg"
        )

    print(f"output_dir={output_dir}")
    print(f"mission_file={mission_file}")
    print(f"ephemeris_file={ephemeris_file}")
    print(f"final_event={event_type} at {location}")
    print(f"final_mass_kg={final_mass:.6f}")
    print(f"worst_constraint={worst_constraint:.6g}")

    if failures:
        raise AssertionError("; ".join(failures))

    print("asteroid integration fixture passed")


def main() -> int:
    args = parse_args()
    emtg = Path(args.emtg).resolve()
    case_file = Path(args.case).resolve()
    output_root = Path(args.output_root).resolve()

    if args.validate_output_dir:
        mission_name = read_mission_name(case_file)
        validate_output_dir(
            Path(args.validate_output_dir).resolve(),
            mission_name,
            args.min_final_mass,
            args.max_worst_constraint,
        )
        return 0

    if not emtg.is_file():
        raise FileNotFoundError(f"EMTG executable not found: {emtg}")
    if not case_file.is_file():
        raise FileNotFoundError(f"Case file not found: {case_file}")

    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = output_root / f"A20136163_AEPS_IPOPT_FBLT_{run_stamp}"
    output_dir.mkdir(parents=True, exist_ok=False)

    run_options = write_run_options(case_file, output_dir, args.run_budget_seconds)
    log_file = output_dir / "emtg_run.log"

    with log_file.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            [str(emtg), str(run_options)],
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=args.timeout,
        )

    if result.returncode != 0:
        raise RuntimeError(f"EMTG exited with code {result.returncode}. See {log_file}")

    validate_output_dir(output_dir, read_mission_name(run_options), args.min_final_mass, args.max_worst_constraint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
