from __future__ import annotations

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
from PyEMTG.Studio.trajectory import (
    OutputFrameMetadata, normalize_samples_to_icrf, parse_output_frame_metadata,
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
