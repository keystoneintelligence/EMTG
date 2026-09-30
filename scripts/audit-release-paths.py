"""Reject machine-local paths in release files and archive members."""
from __future__ import annotations

import argparse
import hashlib
import io
from pathlib import Path
import re
import tarfile
import zipfile


LOCAL_PATH = re.compile(
    # Optimized comparisons can embed short path fragments as instruction
    # immediates. A generic Windows filename must continue to a text boundary.
    # Explicit build roots are still checked in every byte, including code.
    rb"(?<![a-z0-9])(?:[a-z]:[/\\][a-z0-9_. -]{2,}[/\\][\x20-\x7e]*(?=[\x00\r\n]|$)"
    rb"|/(?:home|users|work|workspace|tmp|build)/)"
    rb"|[/\\](?:_local|buildtrees)[/\\]",
    re.IGNORECASE,
)

# Exact, unchanged NASA examples at upstream 5e8817f7. These contain historical
# installation placeholders or Office save-location metadata. Do not rewrite
# scientific assets during packaging. A modified asset loses this exemption;
# explicitly forbidden build roots are always checked, even for these hashes.
UPSTREAM_EXAMPLE_HASHES = {
    "08847daf0e63aabfaccb191d84123819acda3abd55bba4352972d5019f6875da",
    "4fcea8158b87f710833bc3c6de3a627172424c3da706f0089cac26bda233ec88",
    "457d1dd41d2d29e2aaddfaa54002a9d0fd9a014e6e9228698762f509dd88ef28",
    "bd329fae3a3d64f197fe0f8490493aaae6470a1c4fd1dd77934a8a672f993c2a",
    "2b3e61e2f368a0187027b25640db05c189445cc6b0c80febd7b85263ca000023",
}

# Exact NUL-delimited option defaults from upstream missionoptions.cpp at
# 5e8817f7. These installation placeholders are not build-machine locations.
# Match complete strings only; a file beneath one of these paths is not exempt.
UPSTREAM_DEFAULTS = (
    b"c:/emtg/universe",
    b"c:/emtg/hardwaremodels/",
    b"c:/utilities/cspice/exe",
    b"c:/emtg/pyemtg/",
)


def contains_local_path(data: bytes, forbidden_roots=(), *, check_generic=True) -> bool:
    # Decode wide printable strings while preserving ordinary NUL boundaries.
    wide = re.sub(rb"(?:[\x20-\x7e]\0){4,}", lambda m: m[0].replace(b"\0", b""), data)
    wide = re.sub(rb"(?:\0[\x20-\x7e]){4,}", lambda m: m[0].replace(b"\0", b""), wide)
    for value in (data, wide):
        normalized = value.replace(b"\\", b"/").lower()
        if any(
            str(root).replace("\\", "/").rstrip("/").lower().encode("utf-8") in normalized
            for root in forbidden_roots if str(root).rstrip("/\\")
        ):
            return True
        generic = normalized
        for default in UPSTREAM_DEFAULTS:
            generic = re.sub(rb"(?:^|(?<=\x00))" + re.escape(default) + rb"(?=\x00|$)", b"", generic)
        if check_generic and LOCAL_PATH.search(generic):
            return True
    return False


def audit_file(path: Path, forbidden_roots=()) -> list[str]:
    violations = []

    def check(name, data, upstream=False):
        upstream = upstream or hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest() in UPSTREAM_EXAMPLE_HASHES
        upstream = upstream or hashlib.sha256(data).hexdigest() in UPSTREAM_EXAMPLE_HASHES
        if contains_local_path(data, forbidden_roots, check_generic=not upstream):
            violations.append(name)
        if zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as nested:
                for member in nested.infolist():
                    if not member.is_dir():
                        check(name + "!" + member.filename, nested.read(member), upstream)

    if zipfile.is_zipfile(path):
        upstream = hashlib.sha256(path.read_bytes()).hexdigest() in UPSTREAM_EXAMPLE_HASHES
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not member.is_dir():
                    check(member.filename, archive.read(member), upstream)
    elif path.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                if member.isfile():
                    check(member.name, archive.extractfile(member).read())
    else:
        check(path.name, path.read_bytes())
    return violations


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--forbid-root", action="append", default=[])
    args = parser.parse_args(argv)
    failures = []
    for path in args.paths:
        if not path.exists():
            parser.error(f"release input is missing: {path}")
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
        if not files:
            parser.error(f"release input is empty: {path}")
        for item in files:
            failures.extend(f"{item.name}: {name}" for name in audit_file(item, args.forbid_root))
    if failures:
        # Identify affected members without printing the private paths themselves.
        parser.exit(1, "Local paths found in release content:\n" + "\n".join(failures) + "\n")
    print("Release path audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
