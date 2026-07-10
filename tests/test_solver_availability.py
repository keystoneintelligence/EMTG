import json

from SolverAvailability import available_solver_choices, discover_solver_types


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
