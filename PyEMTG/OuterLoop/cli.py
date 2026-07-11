"""Non-GUI outer-loop command-line interface."""

from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import signal
import tempfile
from typing import Any, Sequence

from .archive import ArchiveEntry
from .campaign import Campaign, EvaluatedCandidate
from .config import CampaignConfig, ConfigError, ValidatedConfig
from .reporting import export_run, inspect_candidate, plot_metrics, promote_candidate
from .qualification import qualify_exhaustively, qualify_trials
from .serde import result_to_dict
from .model import EvaluationStatus
from .storage import CampaignStore, atomic_write_json


def _json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, default=str, allow_nan=False))


def _run_directory(value: str) -> Path:
    path = Path(value).resolve()
    if path.is_file() and path.name == "checkpoint.json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return Path(payload.get("run_directory", path.parent)).resolve()
        except (OSError, ValueError):
            return path.parent
    return path


def _run_campaign(campaign: Campaign, max_new_evaluations: int | None = None):
    previous = {}
    def cancel(_signum, _frame):
        campaign.cancel()
    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            number = getattr(signal, name)
            previous[number] = signal.signal(number, cancel)
    try:
        return campaign.run(max_new_evaluations=max_new_evaluations)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outerloop", description="Autonomous discrete EMTG mission-architecture optimizer")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate a campaign before launching jobs")
    validate.add_argument("config")

    run = subparsers.add_parser("run", help="run a new or existing configured campaign")
    run.add_argument("config")
    run.add_argument("--max-new-evaluations", type=int)

    resume = subparsers.add_parser("resume", help="resume an interrupted campaign exactly")
    resume.add_argument("checkpoint")
    resume.add_argument("--max-new-evaluations", type=int)

    status = subparsers.add_parser("status", help="show checkpoint and evaluation state")
    status.add_argument("run_directory")

    export = subparsers.add_parser("export", help="export archive and evaluation tables")
    export.add_argument("run_directory")
    export.add_argument("--output-directory")
    export.add_argument("--status")
    export.add_argument("--feasibility", choices=("feasible", "infeasible"))
    export.add_argument("--body")
    export.add_argument("--hardware")
    export.add_argument("--group")
    export.add_argument(
        "--objective",
        help="filter as NAME, NAME:LOWER, or NAME:LOWER:UPPER",
    )
    export.add_argument("--no-legacy", action="store_true")
    export.add_argument("--fidelity", default=None, help="confirmed, a configured fidelity name, or all")
    export.add_argument("--trial", type=int)
    export.add_argument("--context")
    export.add_argument("--plot", nargs="+", metavar="METRIC")

    inspect = subparsers.add_parser("inspect", help="inspect genotype, phenotype, result, and provenance")
    inspect.add_argument("run_directory")
    inspect.add_argument("candidate_id")

    rerun = subparsers.add_parser("rerun", help="re-evaluate one archived candidate")
    rerun.add_argument("run_directory")
    rerun.add_argument("candidate_id", nargs="?")
    rerun.add_argument("--archive", action="store_true", help="re-evaluate every current archive phenotype")
    rerun.add_argument("--fidelity", help="override the stored/evolution fidelity")
    rerun.add_argument("--allow-context-change", action="store_true")

    promote = subparsers.add_parser("promote", help="write a feasible solution and its optimized trialX as a standalone .emtgopt")
    promote.add_argument("run_directory")
    promote.add_argument("candidate_id")
    promote.add_argument("--output")
    promote.add_argument("--allow-stale-context", action="store_true")

    qualify = subparsers.add_parser("qualify", help="exhaustively enumerate a bounded search space")
    qualify.add_argument("config")
    qualify.add_argument("--maximum-architectures", type=int, default=100000)
    qualify.add_argument("--suite", choices=("exhaustive", "local", "nightly"), default="exhaustive")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            config = CampaignConfig.from_file(args.config)
            with tempfile.TemporaryDirectory(prefix="outerloop-validate-") as temporary:
                temporary_path = Path(temporary)
                validation_config = replace(
                    config,
                    run_directory=temporary_path / "run",
                    cache=ValidatedConfig({"directory": str(temporary_path / "cache")}),
                    checkpoints=ValidatedConfig(),
                    outputs=ValidatedConfig(),
                )
                campaign = Campaign(validation_config)
                _json({
                    "valid": True,
                    "schema_version": config.schema_version,
                    "run_directory": str(config.run_directory),
                    "phenotype_dimensions": {
                        "max_journeys": config.search.max_journeys,
                        "max_flybys_per_journey": config.search.max_flybys,
                    },
                    "objectives": [value.name for value in config.objectives],
                    "operators": list(campaign.operators.names()),
                    "evaluator": campaign.evaluator.context_identity(),
                    "warnings": campaign.store.get_metadata("many_objective_warning", None),
                })
        elif args.command == "run":
            outcome = _run_campaign(Campaign(CampaignConfig.from_file(args.config)), args.max_new_evaluations)
            _json(asdict(outcome))
            return 0 if outcome.complete else 2
        elif args.command == "resume":
            outcome = _run_campaign(Campaign.resume(args.checkpoint), args.max_new_evaluations)
            _json(asdict(outcome))
            return 0 if outcome.complete else 2
        elif args.command == "status":
            _json(CampaignStore(_run_directory(args.run_directory)).status())
        elif args.command == "export":
            filters = {
                key: value
                for key, value in {
                    "status": args.status,
                    "feasibility": args.feasibility,
                    "body": args.body,
                    "hardware": args.hardware,
                    "group": args.group,
                    "objective": args.objective,
                    "trial": str(args.trial) if args.trial is not None else None,
                    "context": args.context,
                }.items()
                if value is not None
            }
            paths = export_run(
                _run_directory(args.run_directory),
                args.output_directory,
                filters=filters,
                legacy=not args.no_legacy,
                fidelity=args.fidelity,
            )
            if args.plot:
                paths["plot"] = str(plot_metrics(_run_directory(args.run_directory), args.plot))
            _json(paths)
        elif args.command == "inspect":
            _json(inspect_candidate(_run_directory(args.run_directory), args.candidate_id))
        elif args.command == "promote":
            _json({"output": str(promote_candidate(
                _run_directory(args.run_directory), args.candidate_id, args.output,
                allow_stale_context=args.allow_stale_context,
            ))})
        elif args.command == "rerun":
            run_directory = _run_directory(args.run_directory)
            campaign = Campaign.resume(run_directory)
            if args.archive:
                identifiers = sorted({record["result"].candidate_id for record in campaign.store.archive_records()})
            elif args.candidate_id:
                identifiers = [args.candidate_id]
            else:
                raise ValueError("provide CANDIDATE_ID or --archive")
            outputs = []
            for identifier in identifiers:
                found = campaign.store.find_candidate(identifier)
                if found is None:
                    raise KeyError(f"candidate not found: {identifier}")
                candidate, previous = found
                fidelity = args.fidelity or (previous.fidelity if previous is not None else campaign.fidelity)
                if fidelity not in campaign._fidelity_names():
                    raise ValueError(f"unknown fidelity {fidelity}")
                campaign._active_fidelity = fidelity
                request = campaign._request(candidate)
                if previous is not None and request.evaluation_key != previous.evaluation_key and not args.allow_context_change:
                    raise ValueError("executable/assets/context changed; pass --allow-context-change to create a distinct evaluation")
                request = replace(
                    request,
                    context={
                        **dict(request.context),
                        "rerun_of": previous.evaluation_key if previous is not None else None,
                        "rerun_attempt": campaign.store.evaluation_attempt_count(
                            previous.evaluation_key if previous is not None else request.evaluation_key
                        ) + 1,
                    },
                )
                base_evaluator = campaign.evaluator
                while hasattr(base_evaluator, "base"):
                    base_evaluator = base_evaluator.base
                if hasattr(base_evaluator, "run_directory"):
                    base_evaluator.run_directory = run_directory / "reruns" / candidate.individual_id
                raw_result = campaign.evaluator.evaluate(request)
                if raw_result.status not in {EvaluationStatus.CANCELLED, EvaluationStatus.PENDING, EvaluationStatus.RUNNING}:
                    campaign.cache.put(raw_result, request.context)
                result = campaign._enrich_result(candidate, raw_result)
                campaign.store.record_evaluation(result)
                individual = campaign._individual(candidate, result)
                campaign._update_archive([EvaluatedCandidate(candidate, result, individual)], candidate.generation)
                output = run_directory / "reruns" / candidate.individual_id / "rerun-result.json"
                atomic_write_json(output, result_to_dict(result))
                outputs.append({"candidate_id": candidate.candidate_id, "result": result_to_dict(result), "record": str(output)})
            _json({"reruns": outputs})
        elif args.command == "qualify":
            campaign = Campaign(CampaignConfig.from_file(args.config))
            if args.suite in {"local", "nightly"}:
                outcome = _run_campaign(campaign)
                if not outcome.complete:
                    raise RuntimeError("qualification campaign did not complete")
                reports, assessment = qualify_trials(
                    campaign,
                    maximum_architectures=args.maximum_architectures,
                    require_production_gate=args.suite == "nightly",
                )
                _json({
                    "suite": args.suite,
                    "reports": [asdict(report) for report in reports],
                    "assessment": asdict(assessment),
                })
            else:
                report = qualify_exhaustively(
                    campaign, maximum_architectures=args.maximum_architectures
                )
                _json(asdict(report))
        return 0
    except (ConfigError, ValueError, KeyError, FileNotFoundError, RuntimeError) as error:
        print(f"outerloop: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
