from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re
from typing import Any, Mapping


def _journey_central_body(base_case: Path) -> str:
    if base_case.is_file():
        for line in base_case.read_text(encoding="utf-8", errors="replace").splitlines():
            match = re.match(r"\s*journey_central_body\s+(\S+)", line)
            if match:
                return match.group(1)
    return "Sun"


def _universe_bodies(path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    in_body_list = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if line == "begin_body_list":
            in_body_list = True
            continue
        if line == "end_body_list":
            break
        if not in_body_list or not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 4:
            continue
        try:
            spice_id = int(fields[3])
        except ValueError:
            continue
        output.append({"name": fields[0], "short_name": fields[1], "spice_id": spice_id})
    return output


def _central_spice_id(path: Path) -> int:
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*central_body_SPICE_ID\s+(-?\d+)", raw)
        if match:
            return int(match.group(1))
    raise ValueError(f"central_body_SPICE_ID is missing from {path}")


def _category(name: str, spice_id: int) -> str:
    if abs(spice_id) >= 10_000_000:
        return "asteroid"
    if name.endswith("_system") or 1 <= spice_id <= 9:
        return "barycenter"
    if spice_id in {301, 401, 402, 802} or 500 < spice_id < 1000:
        return "moon"
    return "planet"


@lru_cache(maxsize=8)
def _discover(universe_folder_value: str, base_case_value: str) -> dict[str, Any]:
    universe_folder = Path(universe_folder_value).resolve()
    base_case = Path(base_case_value).resolve()
    central_body = _journey_central_body(base_case)
    universe_path = universe_folder / f"{central_body}.emtg_universe"
    if not universe_path.is_file():
        return {
            "items": [], "count": 0, "ready": False,
            "central_body": central_body, "central_spice_id": None, "universe_file": str(universe_path),
            "kernel_files": [], "error": "active EMTG universe file was not found",
        }

    kernel_root = universe_folder / "ephemeris_files"
    kernels = sorted(kernel_root.glob("*.bsp")) if kernel_root.is_dir() else []
    object_kernels: dict[int, list[str]] = {}
    spice_error: str | None = None
    try:
        import spiceypy
        for kernel in kernels:
            for value in spiceypy.spkobj(str(kernel)):
                object_kernels.setdefault(int(value), []).append(kernel.name)
    except (ImportError, OSError, RuntimeError) as error:
        spice_error = str(error)

    items = []
    for body in _universe_bodies(universe_path):
        spice_id = int(body["spice_id"])
        supporting_kernels = object_kernels.get(spice_id, [])
        if not supporting_kernels:
            continue
        display_name = str(body["name"]).replace("_", " ")
        items.append({
            **body,
            "display_name": display_name,
            "category": _category(str(body["name"]), spice_id),
            "kernel_files": supporting_kernels,
            "universe_file": universe_path.name,
        })
    category_order = {"planet": 0, "barycenter": 1, "moon": 2, "asteroid": 3}
    items.sort(key=lambda value: (category_order.get(str(value["category"]), 9), str(value["display_name"]).lower()))
    return {
        "items": items,
        "count": len(items),
        "ready": bool(items) and spice_error is None,
        "central_body": central_body,
        "central_spice_id": _central_spice_id(universe_path),
        "universe_file": str(universe_path),
        "kernel_files": [str(value) for value in kernels],
        "error": spice_error,
    }


def discover_bodies(config: Mapping[str, Any]) -> dict[str, Any]:
    assets = config.get("assets", {})
    return _discover(str(assets.get("universe_folder", "")), str(config.get("base_case", "")))
