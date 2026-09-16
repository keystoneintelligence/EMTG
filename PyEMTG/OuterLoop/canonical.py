"""Cross-process canonical serialization and content identities."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from decimal import Decimal
from enum import Enum
import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any, Mapping


class CanonicalizationError(ValueError):
    """Raised when a value has no deterministic JSON representation."""


def _decimal_text(value: float | Decimal) -> str:
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError("NaN and infinity are not canonical values")
        value = Decimal(repr(value))
    if not value.is_finite():
        raise CanonicalizationError("NaN and infinity are not canonical values")
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def canonical_value(value: Any) -> Any:
    """Return a JSON-safe representation with explicit numeric types."""
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, Path):
        return str(value.resolve())
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return {"$decimal": _decimal_text(value)}
    if isinstance(value, bytes):
        return {"$bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError("canonical mapping keys must be strings")
            output[key] = canonical_value(item)
        return output
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [canonical_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
    raise CanonicalizationError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def content_hash(value: Any, *, prefix: str = "") -> str:
    digest = hashlib.sha256()
    digest.update(prefix.encode("utf-8"))
    digest.update(b"\0")
    digest.update(canonical_json(value).encode("utf-8"))
    return digest.hexdigest()


def file_sha256(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


SOURCE_MANIFEST_SCHEMA = 3


def source_manifest(repository_root: str | Path) -> dict[str, Any]:
    """Hash every Python source that can influence case generation/parsing.

    Git metadata is evidence only: the content digest is authoritative and
    therefore changes for dirty, staged, and untracked source alike.
    """
    root = Path(repository_root).resolve()
    selected: list[Path] = []
    outer = root / "PyEMTG" / "OuterLoop"
    if outer.is_dir():
        selected.extend(outer.rglob("*.py"))
    for name in ("MissionOptions.py", "JourneyOptions.py", "Universe.py", "Body.py"):
        path = root / "PyEMTG" / name
        if path.is_file():
            selected.append(path)
    files = {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(set(selected))
    }
    head: str | None = None
    dirty = True
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ["git", "status", "--porcelain", "--", "PyEMTG/OuterLoop", "PyEMTG/MissionOptions.py",
             "PyEMTG/JourneyOptions.py", "PyEMTG/Universe.py", "PyEMTG/Body.py"],
            cwd=root, check=True, capture_output=True, text=True, timeout=10,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "git_head": head,
        "dirty": dirty,
        "content_hash": content_hash(files, prefix="emtg-outerloop-source-v3"),
        "files": files,
    }
