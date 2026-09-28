"""Record and verify source/artifact identity without private deployment policy."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import zipfile

SCHEMA = 1
PLATFORMS = {
    "windows-x64": ("EMTGv9-windows-x64.exe", "EMTG-*-Windows-*.zip", "EMTGv9.exe"),
    "linux-x64-experimental": ("EMTGv9-linux-x64-experimental", "EMTG-*-Linux-*-experimental.tar.gz", "EMTGv9"),
}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def git(root, *args):
    return subprocess.check_output(["git", "-c", f"safe.directory={Path(root).resolve().as_posix()}",
                                    "-C", str(root), *args])


def source_identity(root):
    root = Path(root).resolve()
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all").decode("utf-8")
    # Ignored generated output is allowed; ignored code/configuration in build
    # input directories cannot silently escape a clean-source check.
    ignored = git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z",
                  "--", "src", "Source", "cmake", "packaging", "CMakeUserPresets.json").decode("utf-8").split("\0")
    inputs = sorted(p for p in ignored if p and Path(p).suffix.lower() in
                    {".cpp", ".c", ".h", ".hpp", ".cmake", ".py", ".in", ".json"})
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z").decode("utf-8").split("\0")
    content = {p: digest(root / p) for p in sorted(set(inputs + untracked)) if p and (root / p).is_file()}
    return {
        "revision": git(root, "rev-parse", "HEAD").decode().strip(),
        "tree": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
        "clean": not status and not inputs,
        "changes": status.splitlines(),
        "ignored_build_inputs": inputs,
        "uncommitted_content_sha256": hashlib.sha256(
            git(root, "diff", "--binary", "HEAD") + json.dumps(content, sort_keys=True).encode()).hexdigest(),
    }


def archive_executable_hash(path, executable_name):
    path = Path(path)
    def matches(name):
        parts = PurePosixPath(name).parts
        if name.startswith("/") or ".." in parts or "\\" in name:
            raise ValueError("Unsafe archive path")
        return len(parts) >= 2 and parts[-2:] == ("bin", executable_name)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            found = [item for item in archive.infolist() if matches(item.filename) and not item.is_dir()]
            if len(found) != 1:
                raise ValueError("Expected exactly one archive executable")
            with archive.open(found[0]) as stream:
                return hashlib.file_digest(stream, "sha256").hexdigest()
    with tarfile.open(path, "r:gz") as archive:
        found = [item for item in archive if matches(item.name)]
        if len(found) != 1 or not found[0].isfile():
            raise ValueError("Expected exactly one regular archive executable")
        with archive.extractfile(found[0]) as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()


def write_receipt(root, snapshot, dist, executable, platform, build_info):
    root, dist, executable = Path(root), Path(dist), Path(executable)
    after = source_identity(root)
    if after != snapshot:
        raise ValueError("Source changed during build; rebuild from stable inputs")
    standalone, pattern, binary = PLATFORMS[platform]
    archives = list(dist.glob(pattern))
    if len(archives) != 1:
        raise ValueError("Expected exactly one release archive for this platform")
    expected = digest(executable)
    if digest(dist / standalone) != expected or archive_executable_hash(archives[0], binary) != expected:
        raise ValueError("Packaged and managed executable hashes differ")
    files = {p.name: digest(p) for p in sorted(dist.iterdir())
             if p.is_file() and not p.name.endswith(".provenance.json")}
    receipt = dist / f"EMTG-{platform}.provenance.json"
    write_json(receipt, {
        "schema_version": SCHEMA, "platform": platform,
        "version": (root / "VERSION").read_text().strip(), "source": snapshot,
        "executable_sha256": expected, "standalone": standalone,
        "archive": archives[0].name, "files": files, "build": build_info,
    })
    return receipt


def verify_receipt(receipt, *, root=None, executable=None, expected_build_revision=None,
                   allow_comparison=False, run_metadata=None):
    receipt = Path(receipt)
    data = json.loads(receipt.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != SCHEMA or data.get("platform") not in PLATFORMS:
        raise ValueError("Unsupported release provenance schema/platform")
    if not data["source"]["clean"]:
        raise ValueError("Release qualification requires clean build source")
    revision = data["source"]["revision"]
    if expected_build_revision and revision != expected_build_revision:
        raise ValueError("Receipt build revision differs from expected build revision")
    files = data["files"]
    for name, expected in files.items():
        if Path(name).name != name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError("Unsafe artifact path in receipt")
        path = receipt.parent / name
        if not path.is_file() or digest(path) != expected:
            raise ValueError(f"Artifact hash mismatch: {name}")
    standalone, pattern, binary = PLATFORMS[data["platform"]]
    if data["standalone"] != standalone or data["archive"] not in files or standalone not in files:
        raise ValueError("Missing or invalid artifact identity")
    if not PurePosixPath(data["archive"]).match(pattern):
        raise ValueError("Archive does not match platform")
    expected = data["executable_sha256"]
    if files[standalone] != expected or archive_executable_hash(receipt.parent / data["archive"], binary) != expected:
        raise ValueError("Archive and standalone executable hashes differ")
    if executable and digest(executable) != expected:
        raise ValueError("Test executable hash differs from release")
    if run_metadata:
        ci = data["build"].get("ci", {})
        if (run_metadata["repository"]["full_name"] != ci.get("repository")
                or str(run_metadata["id"]) != str(ci.get("run_id"))
                or str(run_metadata["run_attempt"]) != str(ci.get("run_attempt"))
                or run_metadata["head_sha"] != revision
                or run_metadata["path"].split("@")[0] != ".github/workflows/release.yml"
                or run_metadata["status"] != "completed"):
            raise ValueError("Downloaded artifact provenance differs from selected workflow run")
    current = source_identity(root) if root else None
    if current and not current["clean"]:
        raise ValueError("Qualification requires clean test source")
    same = current is not None and current["revision"] == revision and current["tree"] == data["source"]["tree"]
    if current and not same and not allow_comparison:
        raise ValueError("Build source and test source differ; use explicit comparison mode or rebuild")
    return {
        "schema_version": SCHEMA, "build_source_revision": revision,
        "test_source_revision": current["revision"] if current else None,
        "same_source": same,
        "qualification_scope": ("same-source" if same else "cross-revision-comparison" if current else "artifact-integrity"),
        "executable_sha256": expected, "archive_sha256": files[data["archive"]],
        "receipt_sha256": digest(receipt), "platform": data["platform"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("snapshot")
    capture.add_argument("--root", type=Path, required=True)
    capture.add_argument("--output", type=Path, required=True)
    record = sub.add_parser("record")
    record.add_argument("--root", type=Path, required=True)
    record.add_argument("--snapshot", type=Path, required=True)
    record.add_argument("--dist", type=Path, required=True)
    record.add_argument("--executable", type=Path, required=True)
    record.add_argument("--platform", choices=PLATFORMS, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", type=Path, required=True)
    verify.add_argument("--root", type=Path)
    verify.add_argument("--executable", type=Path)
    verify.add_argument("--expected-build-revision")
    verify.add_argument("--allow-comparison", action="store_true")
    verify.add_argument("--run-metadata", type=Path)
    verify.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            write_json(args.output, source_identity(args.root))
        elif args.command == "record":
            capabilities = json.loads(subprocess.check_output([str(args.executable.resolve()), "--capabilities"], text=True))
            info = {"preset": "windows-release" if args.platform == "windows-x64" else "linux-release", "capabilities": capabilities, "ci": {key: os.getenv(env) for key, env in
                    (("repository", "GITHUB_REPOSITORY"), ("run_id", "GITHUB_RUN_ID"), ("run_attempt", "GITHUB_RUN_ATTEMPT"))}}
            receipt = write_receipt(args.root, json.loads(args.snapshot.read_text(encoding="utf-8-sig")),
                                    args.dist, args.executable, args.platform, info)
            print(receipt)
        else:
            result = verify_receipt(args.receipt, root=args.root, executable=args.executable,
                                   expected_build_revision=args.expected_build_revision,
                                   allow_comparison=args.allow_comparison,
                                   run_metadata=json.loads(args.run_metadata.read_text(encoding="utf-8-sig")) if args.run_metadata else None)
            if args.output:
                write_json(args.output, result)
            print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Provenance verification failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
