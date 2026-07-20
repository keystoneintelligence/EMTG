from __future__ import annotations

from datetime import datetime, timezone
import json
from math import dist
from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyEMTG.Studio.api import create_app
from PyEMTG.Studio.bodies import discover_bodies
from PyEMTG.Studio.body_ephemeris import BodyEphemerisService
from PyEMTG.Studio.ephemeris import SpiceEphemerisProvider, _mjd_tdb_to_et
from PyEMTG.Studio.storage import StudioStore
from PyEMTG.Studio.search_defaults import default_search_configuration
from PyEMTG.Studio.search_effort import PRODUCTION_SEARCH_EFFORT, apply_search_effort
from PyEMTG.Studio.trajectory import (
    OutputFrameMetadata, normalize_samples_to_icrf, parse_dense_ephemeris, parse_output_frame_metadata,
    trajectory_endpoints_align,
)
from PyEMTG.Studio.worker import run_job


REPOSITORY = Path(__file__).resolve().parents[1]


def real_studio_runtime() -> dict:
    """Return local release assets or skip on source-only CI checkouts."""
    defaults = default_search_configuration(REPOSITORY, REPOSITORY)
    kernel_root = (
        Path(str(defaults["config"].get("assets", {}).get("universe_folder", "")))
        / "ephemeris_files"
    )
    required_kernels = (kernel_root / "de430.bsp", kernel_root / "asteroids_100_2026_04_07.bsp")
    if not defaults["ready"] or not all(path.is_file() for path in required_kernels):
        missing = [
            *defaults.get("missing", ()),
            *(str(path) for path in required_kernels if not path.is_file()),
        ]
        pytest.skip("real EMTG/SPICE release assets are unavailable: " + "; ".join(missing))
    return defaults


def configuration() -> dict:
    return {
        "schema_version": "outerloop/v2",
        "run_directory": "ignored-by-studio",
        "root_seed": 17,
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


def test_emtg_j2000_bci_output_is_normalized_to_icrf(tmp_path: Path):
    mission = tmp_path / "arrival.emtg"
    mission.write_text(
        "\n".join((
            "Mission: regression",
            "Journey: 0",
            "Central Body: Sun",
            "Frame: J2000_BCI",
            "alpha0: -1.570796326794896558",
            "delta0: 1.1617035245118223497",
            "      |         (ET/TDB) |",
        )),
        encoding="utf-8",
    )
    metadata = parse_output_frame_metadata(mission)
    assert metadata == OutputFrameMetadata(
        frame="J2000_BCI", central_body="Sun",
        alpha0=-1.5707963267948966, delta0=1.1617035245118223,
        time_system="TDB",
    )
    samples, transformation = normalize_samples_to_icrf([{
        "epoch_mjd": 62141.00000003,
        "position_km": [-318383497.58026218, 271513742.51715690, 8185005.69264045],
        "velocity_km_s": [-9.95544223, -12.02432124, -0.46669708],
    }], metadata)
    assert transformation.startswith("J2000_BCI to ICRF")
    assert samples[0]["position_km"] == pytest.approx(
        [-318383497.58026218, 245853180.30306330, 115511559.71906440], abs=1.0e-6,
    )


def test_emtg_mjd_tdb_maps_directly_to_spice_et():
    assert _mjd_tdb_to_et(51544.5) == 0.0
    assert _mjd_tdb_to_et(62141.0) == pytest.approx(915537600.0)


def test_dense_ephemeris_frame_is_not_rotated_twice_and_boundaries_are_guarded():
    metadata = OutputFrameMetadata(
        "J2000_BCI", "Sun", -1.5707963267948966, 1.1617035245118223, "TDB",
    )
    raw_event = [{
        "epoch_mjd": 63887.936283680145,
        "position_km": [-26713638.9737944, -280543751.12446034, -9987491.85918245],
    }]
    normalized_event, _ = normalize_samples_to_icrf(raw_event, metadata)
    dense_icrf = [{
        "epoch_mjd": 63887.93628367835,
        "position_km": [-26713638.973794, -253421063.409834, -120757239.571172],
    }]
    assert trajectory_endpoints_align(dense_icrf, normalized_event)
    double_rotated, _ = normalize_samples_to_icrf(dense_icrf, metadata)
    assert not trajectory_endpoints_align(double_rotated, normalized_event)


def test_dense_ephemeris_normalizes_propulsion_columns(tmp_path: Path):
    ephemeris = tmp_path / "powered.ephemeris"
    ephemeris.write_text(
        "\n".join((
            "#epoch, x(km), y(km), z(km), vx(km/s), vy(km/s), vz(km/s), mass(kg), ux, uy, uz, ThrustMagnitude(N), MassFlowRate(kg/s), Isp(s), NumberOfActiveThrusters, ActivePower(kW)",
            "2026 Jul 12  12:00:00.000000, 1, 2, 3, 4, 5, 6, 1200, 0.1, 0.2, 0.3, 0.334, 0.00001, 2500, 1, 11.5",
        )),
        encoding="utf-8",
    )
    sample = parse_dense_ephemeris(ephemeris)[0]
    assert sample["mass_kg"] == 1200.0
    assert sample["control"] == [0.1, 0.2, 0.3]
    assert sample["thrust_magnitude_n"] == 0.334
    assert sample["mass_flow_rate_kg_s"] == 0.00001
    assert sample["isp_s"] == 2500.0
    assert sample["active_engines"] == 1.0
    assert sample["active_power_kw"] == 11.5


def test_store_queue_resources_and_recovery(tmp_path: Path):
    database = tmp_path / "studio" / "studio.sqlite"
    store = StudioStore(database, tmp_path)
    first = store.create_job("first", configuration(), 3, True)
    second = store.create_job("second", configuration(), 2, True)
    assert store.next_queued()["id"] == first["id"]
    store.move_job(second["id"], -1)
    assert store.next_queued()["id"] == second["id"]
    store.set_global_core_limit(1)
    assert store.set_requested_cores(first["id"], 8)["effective_cores"] == 1
    store.set_status(first["id"], "running")
    recovered = StudioStore(database, tmp_path)
    assert recovered.job(first["id"])["status"] == "queued"


def test_search_effort_presets_are_persistent_and_default_to_production(tmp_path: Path):
    database = tmp_path / "studio" / "studio.sqlite"
    store = StudioStore(database, tmp_path)
    presets = store.search_effort_presets()
    assert presets["default_id"] == "production"
    assert {value["id"] for value in presets["items"]} == {"smoke", "production"}
    production = next(value for value in presets["items"] if value["id"] == "production")
    assert production["parallel_candidates"] == 10
    assert production["solve_time_seconds"] == 600
    production["name"] = "Local production"
    store.set_search_effort_presets(presets)
    reopened = StudioStore(database, tmp_path, recover=False)
    assert next(
        value for value in reopened.search_effort_presets()["items"]
        if value["id"] == "production"
    )["name"] == "Local production"


def test_apply_search_effort_preserves_non_effort_configuration():
    config = configuration()
    config["evaluator"] = {
        "type": "emtg", "timeout_seconds": 10,
        "budget": {"nlp_solver_type": 2, "quiet_nlp": 1},
    }
    apply_search_effort(config, PRODUCTION_SEARCH_EFFORT)
    assert config["algorithm"] == {
        "population_size": 20, "generations": 4, "tournament_size": 2,
        "stall_generations": 4, "trials": 1,
    }
    assert config["evaluator"]["timeout_seconds"] == 720
    assert config["evaluator"]["budget"] == {
        "nlp_solver_type": 2, "quiet_nlp": 1, "inner_loop": "mbh",
        "mbh_max_run_time": 600, "mbh_max_trials": 200000,
        "nlp_max_run_time": 600, "nlp_major_iterations": 5000,
    }
    assert config["workers"] == {"count": 10}


def test_delete_job_cascades_catalog_and_managed_artifacts(tmp_path: Path):
    database = tmp_path / "studio" / "studio.sqlite"
    store = StudioStore(database, tmp_path)
    job = store.create_job("synthetic", configuration(), 1, False)
    store.set_status(job["id"], "running")
    assert run_job(database, job["id"]) == 0
    completed = StudioStore(database, tmp_path, recover=False)
    solution = completed.solutions({"job_id": job["id"]}, limit=1, offset=0)["items"][0]
    completed.mark_trajectory(solution["id"], "dense", status="requested")
    run_directory = Path(completed.job(job["id"])["run_directory"])
    assert run_directory.is_dir()
    result = completed.delete_job(job["id"])
    assert result["deleted_solutions"] > 0
    assert result["deleted_trajectories"] >= 1
    assert completed.solutions({"job_id": job["id"]}, limit=10, offset=0)["total"] == 0
    assert all(value["id"] != job["id"] for value in completed.jobs())
    assert not run_directory.exists()


def test_real_search_defaults_discover_asteroid_runtime():
    defaults = real_studio_runtime()
    assert defaults["ready"] is True
    assert defaults["config"]["evaluator"]["type"] == "emtg"
    assert defaults["config"]["search"]["fixed_final"] == "A20136163"
    assert "launch_window_open_date" in defaults["config"]["search"]["mission_genes"]
    assert "launch_epoch" not in defaults["config"]["search"]["mission_genes"]
    assert defaults["config"]["algorithm"]["population_size"] == 20
    assert defaults["config"]["algorithm"]["generations"] == 4
    assert defaults["config"]["evaluator"]["budget"]["mbh_max_run_time"] == 600
    assert defaults["config"]["evaluator"]["budget"]["nlp_major_iterations"] == 5000
    assert defaults["config"]["workers"]["count"] == 10
    assert Path(defaults["config"]["assets"]["executable"]).is_file()
    bodies = discover_bodies(defaults["config"])
    assert bodies["ready"] is True
    assert {value["name"] for value in bodies["items"]} >= {"Earth", "A20136163"}
    asteroid = next(value for value in bodies["items"] if value["name"] == "A20136163")
    assert asteroid["spice_id"] == 20136163
    assert "asteroids_100_2026_04_07.bsp" in asteroid["kernel_files"]
    ephemeris = BodyEphemerisService(defaults["config"]).series(
        ["Earth", "A20136163"], 61200.0, 61300.0, 8,
    )
    assert ephemeris["central_body"] == "Sun_A20136163"
    assert ephemeris["sample_count"] == 8
    assert {value["name"] for value in ephemeris["series"]} == {"Earth", "A20136163"}
    assert all(len(value["samples"]) == 8 for value in ephemeris["series"])
    uncovered = BodyEphemerisService(defaults["config"]).series(
        ["Earth", "A20136163"], 60000.0, 60100.0, 8,
    )
    statuses = {value["name"]: value["coverage_status"] for value in uncovered["series"]}
    assert statuses == {"Earth": "covered", "A20136163": "uncovered"}
    assert next(value for value in uncovered["series"] if value["name"] == "A20136163")["samples"] == []
    leap_seconds = REPOSITORY / "testatron" / "universe" / "ephemeris_files" / "naif0012.tls"
    offset = SpiceEphemerisProvider([leap_seconds]).tdb_minus_utc_seconds([61300.0])[0]
    assert offset == pytest.approx(69.1824273893128, abs=1.0e-9)


def test_current_body_ephemeris_is_centered_on_spice_converted_utc():
    defaults = real_studio_runtime()
    moment = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
    current = BodyEphemerisService(defaults["config"]).current_series(
        ["Earth"], points=3, window_days=2.0, moment=moment,
    )
    assert current["current_utc"] == "2026-07-12T12:00:00Z"
    assert current["start_mjd"] == pytest.approx(current["current_epoch_mjd"] - 1.0)
    assert current["end_mjd"] == pytest.approx(current["current_epoch_mjd"] + 1.0)
    assert current["series"][0]["samples"][1]["epoch_mjd"] == pytest.approx(
        current["current_epoch_mjd"]
    )


def test_feasible_asteroid_endpoints_coincide_with_spice_destination():
    config = real_studio_runtime()["config"]
    bci = OutputFrameMetadata(
        "J2000_BCI", "Sun", -1.5707963267948966, 1.1617035245118223, "TDB",
    )
    cases = [
        (bci, {
            "epoch_mjd": 62141.0000000298,
            "position_km": [-318383497.58026218, 271513742.51715690, 8185005.69264045],
        }),
        (OutputFrameMetadata("J2000/ICRF", "Sun", None, None, "TDB"), {
            "epoch_mjd": 63887.93628367835,
            "position_km": [-26713638.973794, -253421063.40983, -120757239.57117],
        }),
    ]
    for metadata, endpoint in cases:
        normalized, _ = normalize_samples_to_icrf([endpoint], metadata)
        epoch = normalized[0]["epoch_mjd"]
        asteroid = BodyEphemerisService(config).series(
            ["A20136163"], epoch - 1.0e-6, epoch + 1.0e-6, 3,
        )["series"][0]["samples"][1]
        assert dist(normalized[0]["position_km"], asteroid["position_km"]) < 0.01


def test_worker_runs_and_catalogs_synthetic_campaign(tmp_path: Path):
    database = tmp_path / "studio" / "studio.sqlite"
    store = StudioStore(database, tmp_path)
    job = store.create_job("synthetic", configuration(), 2, False)
    store.set_status(job["id"], "running")
    assert run_job(database, job["id"]) == 0
    completed = StudioStore(database, tmp_path, recover=False)
    assert completed.job(job["id"])["status"] == "completed"
    solutions = completed.solutions({"feasible": True}, limit=100, offset=0)
    assert solutions["total"] > 0
    assert {value["start_body"] for value in solutions["items"]} == {"Earth"}
    assert {value["end_body"] for value in solutions["items"]} == {"Mars"}


def test_api_auth_schema_and_draft_job(tmp_path: Path):
    token = "test-token"
    app = create_app(tmp_path, tmp_path / "state", token=token)
    with TestClient(app) as client:
        assert client.get("/api/v1/jobs").status_code == 401
        headers = {"X-EMTG-Token": token}
        response = client.post(
            "/api/v1/jobs",
            headers=headers,
            json={"name": "draft", "config": configuration(), "requested_cores": 2, "queue": False},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "draft"
        # Point schema discovery at the real repository metadata while keeping
        # mutable state in tmp_path.
    app = create_app(REPOSITORY, tmp_path / "schema-state", token=token)
    with TestClient(app) as client:
        response = client.get("/api/v1/options/schema", headers={"X-EMTG-Token": token})
        assert response.status_code == 200
        fields = response.json()["items"]
        assert any(value["name"] == "mission_name" for value in fields)
        assert any(value["scope"] == "journey" for value in fields)
        feasibility = next(value for value in fields if value["name"] == "NLP_feasibility_tolerance")
        assert feasibility["default"] == 1.0e-8
        assert feasibility["aliases"] == ["snopt_feasibility_tolerance"]
        assert feasibility["applicable_solvers"] == []
        minor_iterations = next(value for value in fields if value["name"] == "snopt_minor_iterations")
        assert minor_iterations["applicable_solvers"] == [0]


def test_api_manages_search_effort_presets(tmp_path: Path):
    token = "test-token"
    state = tmp_path / "state"
    app = create_app(tmp_path, state, token=token)
    headers = {"X-EMTG-Token": token}
    with TestClient(app) as client:
        response = client.get("/api/v1/search-effort-presets", headers=headers)
        assert response.status_code == 200
        document = response.json()
        assert document["default_id"] == "production"
        production = next(value for value in document["items"] if value["id"] == "production")
        production["parallel_candidates"] = 8
        document["items"].append({
            **production, "id": "deep-search", "name": "Deep search",
            "solve_time_seconds": 1200, "watchdog_seconds": 1320,
        })
        document["default_id"] = "deep-search"
        saved = client.put("/api/v1/search-effort-presets", headers=headers, json=document)
        assert saved.status_code == 200, saved.text
        assert saved.json()["default_id"] == "deep-search"
        assert len(saved.json()["items"]) == 3

        invalid = saved.json()
        invalid["items"][-1]["watchdog_seconds"] = 60
        rejected = client.put("/api/v1/search-effort-presets", headers=headers, json=invalid)
        assert rejected.status_code == 422
        assert "watchdog" in rejected.json()["detail"]

    reopened = StudioStore(state / "studio.sqlite", tmp_path, recover=False)
    assert reopened.search_effort_presets()["default_id"] == "deep-search"


def test_studio_save_accepts_legacy_solver_names_and_emits_canonical_names(tmp_path: Path):
    token = "test-token"
    app = create_app(tmp_path, tmp_path / "state", token=token)
    output = tmp_path / "normalized.emtgopt"
    headers = {"X-EMTG-Token": token}
    mission = {
        "snopt_feasibility_tolerance": 1.0e-7,
        "snopt_optimality_tolerance": 2.0e-7,
        "snopt_major_iterations": 123,
        "snopt_max_run_time": 45,
        "NLP_max_step": 0.5,
    }

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/options/save",
            headers=headers,
            json={"path": str(output), "mission": mission, "journeys": []},
        )

    assert response.status_code == 200, response.text
    option_names = {
        line.split(" ", 1)[0]
        for line in output.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert {
        "NLP_feasibility_tolerance", "NLP_optimality_tolerance",
        "NLP_iteration_limit", "NLP_max_run_time", "snopt_major_step_limit",
    }.issubset(option_names)
    assert not set(mission).intersection(option_names)


def test_api_body_discovery_and_ephemeris_with_real_runtime(tmp_path: Path):
    real_studio_runtime()
    token = "test-token"
    app = create_app(REPOSITORY, tmp_path / "body-state", token=token)
    with TestClient(app) as client:
        headers = {"X-EMTG-Token": token}
        bodies = client.get("/api/v1/bodies", headers=headers).json()
        assert bodies["ready"] is True
        assert any(value["name"] == "Earth" for value in bodies["items"])
        response = client.get(
            "/api/v1/ephemeris/bodies",
            headers={"X-EMTG-Token": token},
            params=[("names", "Earth"), ("names", "A20136163"), ("start_mjd", "61200"), ("end_mjd", "61300"), ("points", "6")],
        )
        assert response.status_code == 200, response.text
        assert response.json()["sample_count"] == 6
        current = client.get(
            "/api/v1/ephemeris/bodies/now",
            headers=headers,
            params=[("names", "Earth"), ("points", "3"), ("window_days", "2")],
        )
        assert current.status_code == 200, current.text
        assert current.json()["sample_count"] == 3
        assert current.json()["start_mjd"] < current.json()["current_epoch_mjd"] < current.json()["end_mjd"]


def test_api_exposes_job_json_and_deletes_completed_run(tmp_path: Path):
    token = "test-token"
    app = create_app(tmp_path, tmp_path / "state", token=token)
    headers = {"X-EMTG-Token": token}
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/jobs", headers=headers,
            json={"name": "delete me", "config": configuration(), "requested_cores": 1, "queue": False},
        ).json()
        assert created["config"]["evaluator"]["type"] == "synthetic"
        response = client.delete(f"/api/v1/jobs/{created['id']}", headers=headers)
        assert response.status_code == 200
        assert response.json()["deleted_job_id"] == created["id"]
        assert client.get("/api/v1/jobs", headers=headers).json()["items"] == []


def test_standalone_archive_open(tmp_path: Path):
    source = tmp_path / "history.emtg_archive"
    source.write_text(
        "reset count,step count,solution timestamp,Objective function\n0,2,1,8.4\n",
        encoding="utf-8",
    )
    app = create_app(tmp_path, tmp_path / "state", token="token")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/standalone/open",
            headers={"X-EMTG-Token": "token"},
            json={"path": str(source)},
        )
        assert response.status_code == 200
        assert response.json()["type"] == "archive"
        assert response.json()["records"][0]["Objective function"] == "8.4"
