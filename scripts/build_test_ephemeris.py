"""Reproduce the bounded regression SPK by copying original NAIF records.

Requires SpiceyPy only for fixture maintenance, never for normal staging/tests.
See https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/cspice/spksub_c.html.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def subtract_intervals(interval, covered):
    """Keep the pieces not already supplied by a higher-priority SPK segment."""
    remaining = [interval]
    for lower, upper in covered:
        pieces = []
        for start, stop in remaining:
            if upper <= start or lower >= stop:
                pieces.append((start, stop))
            else:
                if start < lower:
                    pieces.append((start, lower))
                if upper < stop:
                    pieces.append((upper, stop))
        remaining = pieces
    return remaining


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def build(recipe, source_directory, output):
    import spiceypy as spice

    if output.exists():
        raise ValueError('Output must be a new file; existing scientific files are never overwritten')
    source_paths = []
    for source in recipe['sources_in_load_order']:
        path = source_directory / source['name']
        if not path.is_file() or sha256(path) != source['sha256']:
            raise ValueError(f'Missing or mismatched source: {source["name"]}')
        source_paths.append(path)
    segments = []
    for path in source_paths:
        handle = spice.dafopr(str(path))
        try:
            spice.dafbfs(handle)
            while spice.daffna():
                descriptor = spice.dafgs(5)
                times, integers = spice.dafus(descriptor, 2, 6)
                body = str(int(integers[0]))
                if body in recipe['body_windows_mjd_tdb']:
                    segments.append((path, descriptor.copy(), *map(float, times), body))
        finally:
            spice.dafcls(handle)

    # SPICE loads later files/segments with higher priority. Resolve overlaps
    # before writing, keeping the original polynomial/interpolation records.
    covered = {}
    selected = []
    for path, descriptor, begin, end, body in reversed(segments):
        for first, last in recipe['body_windows_mjd_tdb'][body]:
            start = max(begin, (first - 51544.5) * 86400.0)
            stop = min(end, (last - 51544.5) * 86400.0)
            if start >= stop:
                continue
            for lower, upper in subtract_intervals((start, stop), covered.get(body, [])):
                selected.append((path, descriptor, lower, upper, body))
                covered.setdefault(body, []).append((lower, upper))
    missing = set(recipe['body_windows_mjd_tdb']) - covered.keys()
    if missing:
        raise ValueError(f'Required bodies absent from source segments: {sorted(missing)}')
    output.parent.mkdir(parents=True, exist_ok=True)
    target = spice.spkopn(str(output), 'EMTG native regression fixture', 0)
    try:
        for index, (path, descriptor, lower, upper, body) in enumerate(reversed(selected)):
            handle = spice.dafopr(str(path))
            try:
                # Do not inherit machine paths or unrelated source comments.
                # Provenance and source hashes are recorded in the public recipe.
                spice.spksub(handle, descriptor, f'EMTG test segment {index:04}', lower, upper, target)
            finally:
                spice.dafcls(handle)
    finally:
        spice.spkcls(target)
    return {'sha256': sha256(output), 'bytes': output.stat().st_size,
            'segments': len(selected), 'spiceypy': spice.__version__,
            'cspice': spice.tkvrsn('TOOLKIT')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--recipe', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/ephemeris/recipe.json')
    args = parser.parse_args()
    try:
        result = build(json.loads(args.recipe.read_text(encoding='utf8')), args.sources, args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
