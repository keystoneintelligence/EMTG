from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..OuterLoop.serde import candidate_to_dict, result_to_dict
from ..OuterLoop.storage import CampaignStore
from .storage import StudioStore, utc_now


def _number(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


class SolutionCatalog:
    def __init__(self, store: StudioStore):
        self.store = store

    def ingest_job(self, job_id: str) -> int:
        job = self.store.job(job_id)
        run_directory = Path(job["run_directory"])
        database = run_directory / "campaign.sqlite"
        if not database.is_file():
            return 0
        campaign = CampaignStore(run_directory)
        archive_keys = {
            record["result"].evaluation_key for record in campaign.archive_records()
        }
        inserted = 0
        for record in campaign.generation_records():
            candidate = record["candidate"]
            result = record.get("result")
            if result is None:
                continue
            metrics = dict(result.metrics)
            events = metrics.get("mission_events", ())
            journeys = candidate.phenotype.journeys
            start_body = journeys[0].departure if journeys else None
            end_body = journeys[-1].arrival if journeys else None
            launch = _number(metrics.get("launch_epoch"))
            arrival = None
            if events and isinstance(events, list):
                arrival = _number(events[-1].get("julian_date_mjd"))
            if arrival is None and launch is not None:
                flight = _number(metrics.get("flight_time"))
                arrival = launch + flight if flight is not None else None
            hardware = " | ".join(
                str(metrics.get(name))
                for name in ("selected_launch_vehicle", "selected_electric_propulsion_system")
                if metrics.get(name) is not None
            )
            solution_id = result.evaluation_key
            now = utc_now()
            self.store.upsert_solution({
                "id": solution_id,
                "evaluation_key": result.evaluation_key,
                "job_id": job_id,
                "candidate_id": candidate.candidate_id,
                "individual_id": candidate.individual_id,
                "status": result.status.value,
                "feasible": 1 if result.feasible else 0,
                "pareto": 1 if result.evaluation_key in archive_keys else 0,
                "fidelity": result.fidelity,
                "trial": record["trial"],
                "generation": record["generation"],
                "role": record["role"],
                "start_body": start_body,
                "end_body": end_body,
                "sequence_text": candidate.phenotype.sequence_text,
                "launch_mjd": launch,
                "arrival_mjd": arrival,
                "flight_time_days": _number(metrics.get("flight_time")),
                "propellant_used_kg": _number(metrics.get("total_propellant_used", metrics.get("total_propellant"))),
                "delivered_mass_kg": _number(metrics.get("delivered_mass")),
                "deterministic_delta_v_km_s": _number(metrics.get("deterministic_delta_v")),
                "thrust_min_n": _number(metrics.get("thrust_min")),
                "thrust_max_n": _number(metrics.get("thrust_max")),
                "duty_cycle": _number(metrics.get("thruster_duty_cycle")),
                "active_engines": int(_number(metrics.get("number_of_thrusters")) or 0) or None,
                "bus_power_kw": _number(metrics.get("bus_power")),
                "hardware_text": hardware,
                "candidate_json": json.dumps(candidate_to_dict(candidate), sort_keys=True, allow_nan=False),
                "result_json": json.dumps(result_to_dict(result), sort_keys=True, allow_nan=False),
                "created_at": now,
                "updated_at": now,
            })
            if events:
                self.store.mark_trajectory(
                    solution_id, "events", status="available", sample_count=len(events), frame="J2000"
                )
            if result.evaluation_key in archive_keys and result.artifacts.get("options"):
                existing_dense = self.store.trajectory(solution_id, "dense")
                if existing_dense is None:
                    self.store.mark_trajectory(solution_id, "dense", status="requested", frame="J2000")
            for role, artifact in result.artifacts.items():
                if str(artifact).lower().endswith(".ephemeris"):
                    self.store.mark_trajectory(
                        solution_id, "dense", status="available", artifact_path=str(artifact), frame="J2000"
                    )
            inserted += 1
        return inserted
