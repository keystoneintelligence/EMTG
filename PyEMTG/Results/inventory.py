"""Describe native artifacts without storage identifiers or mission assumptions."""
from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any, Mapping

ARTIFACT_INVENTORY_VERSION = 'emtg-artifacts/v1'
NATIVE_EVENT_UNITS = {'position': 'km', 'velocity': 'km/s', 'mass': 'kg', 'thrust': 'N', 'power': 'kW', 'epoch': 'JD', 'duration': 'day'}

def _journeys(path: Path) -> list[dict[str, Any]]:
    journeys = []
    current = None
    for line in path.read_text(encoding='utf8', errors='replace').splitlines():
        key, separator, value = line.strip().partition(':')
        if not separator:
            continue
        if key == 'Journey':
            current = {'journey': value.strip() or None, 'central_body': None, 'frame': None}
            journeys.append(current)
        elif key in ('Central Body', 'Frame'):
            if current is None:
                current = {'journey': None, 'central_body': None, 'frame': None}
                journeys.append(current)
            current['central_body' if key == 'Central Body' else 'frame'] = value.strip() or None
    return journeys

def artifact_inventory(artifacts: Mapping[str, str | Path | None], *, time_system: str | None = None, contexts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Hash files and report missing artifacts; unknown context remains null/empty.

    Paths in the result are basenames, never machine-specific source paths.
    Native event-table units are documented EMTG units. Other artifact units and
    any time-system declaration must be provided by the caller. This does not
    convert coordinates, sanitize native files, or decide scientific feasibility.
    """
    entries = []
    for role, value in artifacts.items():
        path = Path(value) if value is not None else None
        present = path is not None and path.is_file()
        context = dict((contexts or {}).get(role, {}))
        digest = None
        if present:
            hasher = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        entries.append({'role': role, 'path': path.name if path else None,
                        'status': 'present' if present else 'missing',
                        'size_bytes': path.stat().st_size if present else None,
                        'sha256': digest,
                        'units': context.get('units', dict(NATIVE_EVENT_UNITS) if role == 'emtg-output' else {}),
                        'time_system': context.get('time_system', time_system),
                        'journeys': context.get('journeys', _journeys(path) if present and role == 'emtg-output' else [])})
    return {'schema_version': ARTIFACT_INVENTORY_VERSION, 'artifacts': entries}
