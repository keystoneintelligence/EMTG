from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import csv
from dataclasses import asdict
import json
from pathlib import Path
import secrets
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ..JourneyOptions import JourneyOptions
from ..MissionOptions import MissionOptions
from ..OuterLoop.config import CampaignConfig, ConfigError
from ..OuterLoop.evaluator import EMTGResultParser
from ..OuterLoop.legacy import read_legacy_nsgaii
from .catalog import SolutionCatalog
from .bodies import discover_bodies
from .body_ephemeris import BodyEphemerisService
from .models import (
    FileRequest, FileWriteRequest, GlobalResourceUpdate, JobCreate, OptionDocument,
    ResourceUpdate,
)
from .options_schema import load_option_schema
from .scheduler import StudioScheduler
from .search_defaults import default_search_configuration
from .storage import StudioStore
from .trajectory import TrajectoryService


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _option_values(value: Any, excluded: set[str] = set()) -> dict[str, Any]:
    return {
        name: _jsonable(item)
        for name, item in vars(value).items()
        if not name.startswith("_") and name not in excluded and not callable(item)
    }


def create_app(
    workspace: str | Path,
    state_root: str | Path | None = None,
    *,
    token: str | None = None,
) -> FastAPI:
    repository = Path(workspace).resolve()
    bundled_root = Path(__file__).resolve().parents[2]
    option_metadata_root = repository if (repository / "OptionsOverhaul").is_dir() else bundled_root
    root = Path(state_root).resolve() if state_root else repository / "_local" / "studio"
    access_token = token or secrets.token_urlsafe(24)
    store = StudioStore(root / "studio.sqlite", repository)
    catalog = SolutionCatalog(store)
    trajectories = TrajectoryService(store)
    scheduler = StudioScheduler(store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()

    app = FastAPI(title="EMTG Studio", version="1.0.0", lifespan=lifespan)
    app.state.store = store
    app.state.scheduler = scheduler
    app.state.access_token = access_token
    app.state.workspace = repository

    def authorize(
        authorization: str | None = Header(default=None),
        x_emtg_token: str | None = Header(default=None),
        access_token_query: str | None = Query(default=None, alias="access_token"),
    ) -> None:
        supplied = access_token_query or x_emtg_token
        if authorization and authorization.lower().startswith("bearer "):
            supplied = authorization[7:]
        if not supplied or not secrets.compare_digest(supplied, access_token):
            raise HTTPException(status_code=401, detail="invalid EMTG Studio access token")

    auth = Depends(authorize)

    def trusted_path(value: str, *, must_exist: bool = False) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = repository / path
        path = path.resolve()
        try:
            path.relative_to(repository)
        except ValueError as error:
            raise HTTPException(status_code=403, detail="path is outside the trusted workspace") from error
        if must_exist and not path.exists():
            raise HTTPException(status_code=404, detail="path does not exist")
        return path

    @app.get("/api/v1/health")
    def health(_: None = auth):
        return {"status": "ok", "workspace": str(repository), "global_core_limit": store.global_core_limit()}

    @app.get("/api/v1/jobs")
    def list_jobs(_: None = auth):
        return {"items": store.jobs(), "global_core_limit": store.global_core_limit()}

    @app.get("/api/v1/search/defaults")
    def search_defaults(_: None = auth):
        return default_search_configuration(repository, bundled_root)

    @app.get("/api/v1/bodies")
    def bodies(_: None = auth):
        defaults = default_search_configuration(repository, bundled_root)
        return discover_bodies(defaults["config"])

    @app.get("/api/v1/ephemeris/bodies")
    def body_ephemerides(
        names: list[str] = Query(default=[]),
        start_mjd: float = Query(...),
        end_mjd: float = Query(...),
        points: int = Query(default=360, ge=2, le=2000),
        frame: str = "J2000",
        _: None = auth,
    ):
        defaults = default_search_configuration(repository, bundled_root)
        try:
            return BodyEphemerisService(defaults["config"]).series(
                names, start_mjd, end_mjd, points, frame,
            )
        except (FileNotFoundError, ValueError, RuntimeError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/v1/jobs")
    def create_job(request: JobCreate, _: None = auth):
        try:
            source = dict(request.config)
            source["run_directory"] = str(root / "validation-placeholder")
            config = CampaignConfig.from_dict(source, repository / "studio-config.json")
            path_errors = config.validate_paths()
            if path_errors:
                raise ConfigError("; ".join(path_errors))
        except (ConfigError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        job = store.create_job(request.name, request.config, request.requested_cores, request.queue)
        scheduler.wake()
        return job

    @app.delete("/api/v1/jobs/{job_id}")
    def delete_job(job_id: str, _: None = auth):
        try:
            result = store.delete_job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        scheduler.wake()
        return result

    @app.post("/api/v1/jobs/{job_id}/validate")
    def validate_job(job_id: str, _: None = auth):
        try:
            job = store.job(job_id)
            source = dict(job["config"])
            source["run_directory"] = job["run_directory"]
            config = CampaignConfig.from_dict(source, repository / "studio-config.json")
            return {"valid": True, "schema_version": config.schema_version, "run_directory": str(config.run_directory)}
        except (KeyError, ConfigError, ValueError, TypeError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/api/v1/jobs/{job_id}/queue")
    def queue_job(job_id: str, _: None = auth):
        try:
            value = store.set_status(job_id, "queued")
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        scheduler.wake()
        return value

    @app.post("/api/v1/jobs/{job_id}/pause")
    def pause_job(job_id: str, apply_now: bool = False, _: None = auth):
        try:
            job = store.job(job_id)
            if job["status"] == "running":
                store.set_status(job_id, "pausing")
                if apply_now:
                    scheduler.interrupt(job_id, "paused")
            elif job["status"] == "queued":
                store.set_status(job_id, "paused")
            return store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.post("/api/v1/jobs/{job_id}/resume")
    def resume_job(job_id: str, _: None = auth):
        try:
            value = store.set_status(job_id, "queued")
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error
        scheduler.wake()
        return value

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, _: None = auth):
        try:
            store.set_status(job_id, "cancelled")
            scheduler.interrupt(job_id, "cancelled")
            return store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.post("/api/v1/jobs/{job_id}/move/{direction}")
    def move_job(job_id: str, direction: str, _: None = auth):
        if direction not in {"up", "down"}:
            raise HTTPException(status_code=422, detail="direction must be up or down")
        store.move_job(job_id, -1 if direction == "up" else 1)
        return store.job(job_id)

    @app.patch("/api/v1/jobs/{job_id}/resources")
    def update_resources(job_id: str, request: ResourceUpdate, _: None = auth):
        try:
            value = store.set_requested_cores(job_id, request.requested_cores)
            if request.apply_now and value["status"] == "running":
                store.set_status(job_id, "pausing")
                scheduler.interrupt(job_id, "paused")
                store.set_status(job_id, "queued")
                scheduler.wake()
            return store.job(job_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="job not found") from error

    @app.patch("/api/v1/resources")
    def update_global_resources(request: GlobalResourceUpdate, _: None = auth):
        store.set_global_core_limit(request.global_core_limit)
        return {"global_core_limit": store.global_core_limit()}

    @app.get("/api/v1/solutions")
    def list_solutions(
        start_body: str | None = None, end_body: str | None = None, sequence: str | None = None,
        status: str | None = None, fidelity: str | None = None, job_id: str | None = None,
        feasible: bool | None = None, launch_after: float | None = None, launch_before: float | None = None,
        arrival_after: float | None = None, arrival_before: float | None = None,
        propellant_min: float | None = None, propellant_max: float | None = None,
        thrust_min: float | None = None, thrust_max: float | None = None,
        limit: int = Query(default=100, ge=1, le=1000), offset: int = Query(default=0, ge=0),
        _: None = auth,
    ):
        return store.solutions(locals(), limit=limit, offset=offset)

    @app.get("/api/v1/solutions/{solution_id}")
    def solution_detail(solution_id: str, _: None = auth):
        try:
            return store.solution(solution_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="solution not found") from error

    @app.get("/api/v1/solutions/{solution_id}/trajectory")
    def solution_trajectory(
        solution_id: str, detail: str = "auto", frame: str = "J2000",
        max_points: int = Query(default=10000, ge=2, le=50000), _: None = auth,
    ):
        if detail not in {"auto", "events", "dense"}:
            raise HTTPException(status_code=422, detail="detail must be auto, events, or dense")
        try:
            return trajectories.get(solution_id, detail, frame, max_points)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="solution not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/api/v1/solutions/{solution_id}/materialize")
    def materialize(solution_id: str, _: None = auth):
        try:
            solution = store.solution(solution_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="solution not found") from error
        for artifact in solution["result"].get("artifacts", {}).values():
            if str(artifact).lower().endswith(".ephemeris") and Path(artifact).is_file():
                store.mark_trajectory(solution_id, "dense", status="available", artifact_path=str(artifact))
                return {"status": "available", "artifact": str(artifact)}
        store.mark_trajectory(solution_id, "dense", status="requested")
        return {"status": "requested", "detail": "Event trajectory remains available while propagation materialization is pending."}

    @app.get("/api/v1/options/schema")
    def option_schema(_: None = auth):
        return {"items": load_option_schema(option_metadata_root)}

    @app.post("/api/v1/options/open")
    def open_options(request: FileRequest, _: None = auth):
        path = trusted_path(request.path, must_exist=True)
        options = MissionOptions(str(path))
        if not options.success:
            raise HTTPException(status_code=422, detail="unable to parse options file")
        return OptionDocument(
            path=str(path),
            mission=_option_values(options, {"Journeys", "success"}),
            journeys=[_option_values(journey) for journey in options.Journeys],
        )

    @app.post("/api/v1/options/save")
    def save_options(document: OptionDocument, _: None = auth):
        if not document.path:
            raise HTTPException(status_code=422, detail="path is required")
        path = trusted_path(document.path)
        options = MissionOptions()
        for name, value in document.mission.items():
            if hasattr(options, name) and name != "Journeys":
                setattr(options, name, value)
        options.Journeys = []
        for raw in document.journeys:
            journey = JourneyOptions()
            for name, value in raw.items():
                if hasattr(journey, name):
                    setattr(journey, name, value)
            options.Journeys.append(journey)
        options.number_of_journeys = len(options.Journeys)
        path.parent.mkdir(parents=True, exist_ok=True)
        options.write_options_file(str(path), not bool(options.print_only_non_default_options))
        return {"saved": str(path)}

    @app.post("/api/v1/files/read")
    def read_file(request: FileRequest, _: None = auth):
        path = trusted_path(request.path, must_exist=True)
        return {"path": str(path), "content": path.read_text(encoding="utf-8", errors="replace")}

    @app.post("/api/v1/files/write")
    def write_file(request: FileWriteRequest, _: None = auth):
        path = trusted_path(request.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(request.content, encoding="utf-8")
        return {"saved": str(path)}

    @app.post("/api/v1/standalone/open")
    def open_standalone(request: FileRequest, _: None = auth):
        path = trusted_path(request.path, must_exist=True)
        suffix = path.suffix.lower()
        if suffix == ".emtg":
            parsed = EMTGResultParser().parse(path, failure_file=path.name.startswith("FAILURE_"))
            return {"type": "mission", "path": str(path), "data": asdict(parsed)}
        if suffix == ".nsgaii":
            population = read_legacy_nsgaii(path)
            return {
                "type": "population", "path": str(path), "headers": population.headers,
                "gene_headers": population.gene_headers,
                "records": [dict(record.values) for record in population.records],
            }
        if suffix == ".emtg_archive":
            with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
                rows = list(csv.DictReader(stream))
            return {"type": "archive", "path": str(path), "records": rows}
        raise HTTPException(status_code=422, detail="supported standalone types are .emtg, .emtg_archive, and .NSGAII")

    @app.websocket("/api/v1/events")
    async def events(websocket: WebSocket):
        supplied = websocket.query_params.get("access_token", "")
        origin = websocket.headers.get("origin")
        if not secrets.compare_digest(supplied, access_token) or (
            origin and not (origin.startswith("http://127.0.0.1") or origin.startswith("http://localhost"))
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        try:
            while True:
                await websocket.send_json({
                    "type": "snapshot", "jobs": store.jobs(),
                    "global_core_limit": store.global_core_limit(),
                })
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    frontend = Path(__file__).resolve().parent / "frontend" / "dist"
    if frontend.is_dir():
        assets = frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}")
        def frontend_route(full_path: str):
            selected = frontend / full_path
            if full_path and selected.is_file() and frontend in selected.resolve().parents:
                return FileResponse(selected)
            return FileResponse(frontend / "index.html")
    else:
        @app.get("/")
        def missing_frontend():
            return HTMLResponse("<h1>EMTG Studio</h1><p>Frontend assets are not built. Run npm.cmd run build in PyEMTG/Studio/frontend.</p>")

    return app
