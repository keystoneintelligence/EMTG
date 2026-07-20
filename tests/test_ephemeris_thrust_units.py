import re
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PHASE_SOURCE_ROOT = REPOSITORY_ROOT / "src" / "Mission" / "Journey" / "Phase"
EPHEMERIS_CALL = re.compile(
    r"write_ephemeris_line\s*\(\s*outputfile\s*,(?P<arguments>.*?)\);",
    re.DOTALL,
)


def _thrusting_ephemeris_calls() -> list[tuple[Path, str]]:
    calls: list[tuple[Path, str]] = []
    for source in PHASE_SOURCE_ROOT.rglob("*.cpp"):
        text = source.read_text(encoding="utf-8", errors="replace")
        for match in EPHEMERIS_CALL.finditer(text):
            arguments = match.group("arguments")
            if "getEPthrust" in arguments or "max_thrust[" in arguments:
                calls.append((source, arguments))
    return calls


def test_dense_ephemeris_writer_contract_is_newtons():
    declaration = (REPOSITORY_ROOT / "src" / "Utilities" / "writey_thing.h").read_text(encoding="utf-8")
    definition = (REPOSITORY_ROOT / "src" / "Utilities" / "writey_thing.cpp").read_text(encoding="utf-8")
    mission = (REPOSITORY_ROOT / "src" / "Mission" / "mission.cpp").read_text(encoding="utf-8")

    assert "ThrustMagnitudeNewtons" in declaration
    assert "ThrustMagnitudeNewtons" in definition
    assert "ThrustMagnitude(N)" in mission


def test_dense_ephemeris_thrusting_phases_pass_newtons():
    calls = _thrusting_ephemeris_calls()

    # Seven paths use the propulsion model's native N output directly. MGALT
    # has two paths whose cached max_thrust is kN for the equations of motion.
    direct_newton_calls = [(source, arguments) for source, arguments in calls if "getEPthrust" in arguments]
    cached_kilonewton_calls = [(source, arguments) for source, arguments in calls if "max_thrust[" in arguments]

    assert len(direct_newton_calls) == 7
    assert len(cached_kilonewton_calls) == 2
    for source, arguments in direct_newton_calls:
        assert "1.0e-3" not in arguments, f"{source} converts N to kN before writing an N column"
    for source, arguments in cached_kilonewton_calls:
        assert "1000.0" in arguments, f"{source} does not convert cached kN back to N for ephemeris output"
