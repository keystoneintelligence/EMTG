from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import traceback

from ..OuterLoop.campaign import Campaign
from ..OuterLoop.config import CampaignConfig
from .catalog import SolutionCatalog
from .storage import StudioStore


def run_job(database: str | Path, job_id: str) -> int:
    seed = StudioStore(database, Path(database).resolve().parents[2], recover=False)
    job = seed.job(job_id)
    store = StudioStore(database, job["source_root"], recover=False)
    catalog = SolutionCatalog(store)
    run_directory = Path(job["run_directory"])
    run_directory.mkdir(parents=True, exist_ok=True)
    try:
        source = dict(job["config"])
        source["run_directory"] = str(run_directory)
        source_path = Path(job["source_root"]) / "studio-config.json"
        config = CampaignConfig.from_dict(source, source_path)
        (run_directory / "studio-source-config.json").write_text(
            json.dumps(source, sort_keys=True, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        campaign = Campaign.resume(run_directory) if (run_directory / "resolved-config.json").is_file() else Campaign(config)
        while True:
            job = store.job(job_id)
            if job["status"] == "cancelled":
                catalog.ingest_job(job_id)
                return 2
            if job["status"] == "pausing":
                store.set_status(job_id, "paused")
                catalog.ingest_job(job_id)
                return 2
            effective = min(int(job["requested_cores"]), store.global_core_limit())
            if hasattr(campaign.backend, "max_workers"):
                campaign.backend.max_workers = effective
            outcome = campaign.run(max_new_evaluations=effective)
            catalog.ingest_job(job_id)
            checkpoint = campaign.store.load_checkpoint() or {}
            store.update_progress(job_id, {
                **asdict(outcome),
                "checkpoint_status": checkpoint.get("status"),
                "effective_cores": effective,
            })
            if outcome.complete:
                store.set_status(job_id, "completed")
                return 0
    except Exception as error:
        try:
            catalog.ingest_job(job_id)
        except Exception:
            pass
        store.set_status(job_id, "failed", error=f"{error}\n{traceback.format_exc()[-8000:]}")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = "1"
    return run_job(args.database, args.job)


if __name__ == "__main__":
    raise SystemExit(main())
