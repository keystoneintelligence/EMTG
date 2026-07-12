from __future__ import annotations

import argparse
from pathlib import Path
import secrets
import socket
import sys
import threading
import webbrowser

import uvicorn

from .api import create_app


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main(argv: list[str] | None = None) -> int:
    repository = (
        Path(sys.executable).resolve().parent
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parents[2]
    )
    parser = argparse.ArgumentParser(prog="emtg-studio")
    parser.add_argument("--workspace", default=str(repository))
    parser.add_argument("--state-root")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--token", help=argparse.SUPPRESS)
    parser.add_argument("--worker-database", help=argparse.SUPPRESS)
    parser.add_argument("--worker-job", help=argparse.SUPPRESS)
    parser.add_argument("--materialize-database", help=argparse.SUPPRESS)
    parser.add_argument("--materialize-solution", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker_database and args.worker_job:
        from .worker import run_job
        return run_job(args.worker_database, args.worker_job)
    if args.materialize_database and args.materialize_solution:
        from .materialize import materialize_solution
        return materialize_solution(args.materialize_database, args.materialize_solution)
    token = args.token or secrets.token_urlsafe(24)
    port = args.port or _free_port()
    app = create_app(args.workspace, args.state_root, token=token)
    url = f"http://127.0.0.1:{port}/?access_token={token}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"EMTG Studio: {url}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
