import json
from unittest.mock import Mock, patch

from SolverAvailability import available_solver_choices, discover_solver_types, read_solver_capabilities


def test_solver_environment_preserves_non_contiguous_ids_and_hides_worhp():
    assert discover_solver_types(environ={"EMTG_AVAILABLE_NLP_SOLVERS": "ipopt"}) == [2]
    assert discover_solver_types(environ={"EMTG_AVAILABLE_NLP_SOLVERS": "SNOPT,2,1"}) == [0, 2]


def test_solver_capability_manifest_controls_choices(tmp_path):
    manifest = tmp_path / "solver_capabilities.json"
    manifest.write_text(
        json.dumps({"schema_version": 1, "snopt": False, "ipopt": True}),
        encoding="utf-8",
    )

    assert discover_solver_types(manifest, environ={}) == [2]
    assert available_solver_choices(capability_file=manifest, environ={}) == [(2, "IPOPT")]


def test_no_build_manifest_falls_back_to_supported_backends_without_worhp(tmp_path):
    missing = tmp_path / "missing.json"
    assert discover_solver_types(missing, environ={}) == [0, 2]


def test_executable_capabilities_are_authoritative_over_compatibility_sidecar(tmp_path):
    executable = tmp_path / "EMTGv9.exe"
    executable.write_bytes(b"placeholder")
    manifest = tmp_path / "solver_capabilities.json"
    manifest.write_text(
        json.dumps({"snopt": True, "ipopt": False, "supported_phase_types": [2]}),
        encoding="utf-8",
    )
    completed = Mock(returncode=0, stdout='{"snopt":false,"ipopt":true}\n')
    with patch("SolverAvailability.subprocess.run", return_value=completed):
        capabilities = read_solver_capabilities(manifest, executable, environ={})
        assert capabilities == {"snopt": False, "ipopt": True}
        assert discover_solver_types(manifest, executable, environ={}) == [2]
