"""Prepare a qualified Community Edition draft for final maintainer review."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess

from release_provenance import PLATFORMS, digest, git, verify_receipt


def run_gh(arguments):
    return subprocess.run(["gh", *arguments], capture_output=True, text=True)


def prepare_assets(root, artifacts, tag, expected_run_id=None):
    root, artifacts = Path(root).resolve(), Path(artifacts).resolve()
    version = (root / "VERSION").read_text().strip()
    if tag != "v" + version:
        raise ValueError("Release tag must match VERSION")
    revision = git(root, "rev-parse", "HEAD").decode().strip()
    if git(root, "rev-parse", tag + "^{commit}").decode().strip() != revision:
        raise ValueError("Release tag must point to the checked-out source")
    notes = root / "docs/releases" / (version + ".md")
    if not notes.is_file():
        raise ValueError("Reviewed release notes are missing")
    assets = {}
    for platform in PLATFORMS:
        receipt = artifacts / ("EMTG-" + platform + ".provenance.json")
        verify_receipt(receipt, root=root, expected_build_revision=revision)
        data = json.loads(receipt.read_text(encoding="utf-8-sig"))
        if data["platform"] != platform or data["version"] != version:
            raise ValueError("Receipt platform/version differs from release")
        if expected_run_id and str(data["build"].get("ci", {}).get("run_id")) != str(expected_run_id):
            raise ValueError("Receipt is from a different workflow run")
        for name, checksum in {**data["files"], receipt.name: digest(receipt)}.items():
            if name in assets and assets[name] != checksum:
                raise ValueError("Release assets disagree on a shared filename")
            assets[name] = checksum
    unexpected = sorted(p.name for p in artifacts.iterdir() if p.is_file() and p.name not in assets and p.name != "SHA256SUMS")
    if unexpected:
        raise ValueError("Unreceipted release assets: " + ", ".join(unexpected))
    checksums = artifacts / "SHA256SUMS"
    checksums.write_text("".join(checksum + "  " + name + "\n" for name, checksum in sorted(assets.items())), encoding="utf-8")
    files = [str(artifacts / name) for name in sorted(assets)] + [str(checksums)]
    return version, revision, notes, files


def prepare_draft(root, artifacts, tag, repository, expected_run_id=None):
    version, revision, notes, files = prepare_assets(root, artifacts, tag, expected_run_id)
    common = ["--repo", repository]
    view = run_gh(["release", "view", tag, *common, "--json", "isDraft"])
    if view.returncode == 0:
        if not json.loads(view.stdout)["isDraft"]:
            raise ValueError("Published releases cannot be overwritten")
    elif view.stderr.strip().lower() != "release not found":
        raise RuntimeError("Cannot inspect release: " + view.stderr.strip())
    else:
        created = run_gh(["release", "create", tag, *common, "--draft", "--verify-tag", "--target", revision,
                          "--title", "EMTG Community Edition " + version, "--notes-file", str(notes)])
        if created.returncode:
            raise RuntimeError("Cannot create draft: " + created.stderr.strip())
    uploaded = run_gh(["release", "upload", tag, *files, *common, "--clobber"])
    if uploaded.returncode:
        raise RuntimeError("Cannot attach qualified assets: " + uploaded.stderr.strip())
    updated = run_gh(["release", "edit", tag, *common, "--draft", "--verify-tag", "--target", revision,
                      "--title", "EMTG Community Edition " + version, "--notes-file", str(notes)])
    if updated.returncode:
        raise RuntimeError("Cannot update draft: " + updated.stderr.strip())
    print("Qualified release draft prepared for final maintainer review: " + tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-run-id", default=os.getenv("GITHUB_RUN_ID"))
    args = parser.parse_args()
    try:
        prepare_draft(args.root, args.artifacts, args.tag, args.repo, args.expected_run_id)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        parser.exit(1, "Release preparation failed: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
