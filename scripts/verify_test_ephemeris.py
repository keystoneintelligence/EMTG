"""Compare fixture states with the recorded full SPK set, including boundaries."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_test_ephemeris import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--recipe', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/ephemeris/recipe.json')
    args = parser.parse_args()
    import numpy as np
    import spiceypy as spice

    recipe = json.loads(args.recipe.read_text(encoding='utf8'))
    paths = []
    for source in recipe['sources_in_load_order']:
        path = args.sources / source['name']
        if not path.is_file() or sha256(path) != source['sha256']:
            parser.error(f'Missing or mismatched source: {source["name"]}')
        paths.append(path)
    queries = set()
    for body, windows in recipe['body_windows_mjd_tdb'].items():
        for lower, upper in windows:
            for mjd in np.linspace(lower, upper, 257):
                for observer in (0, 10):
                    queries.add((int(body), float((mjd - 51544.5) * 86400.0), observer))
    handle = spice.dafopr(str(args.kernel))
    try:
        spice.dafbfs(handle)
        while spice.daffna():
            times, integers = spice.dafus(spice.dafgs(5), 2, 6)
            body = int(integers[0])
            for epoch in times:
                for offset in (-1.0, 0.0, 1.0):
                    t = float(epoch) + offset
                    if any(lo <= t / 86400.0 + 51544.5 <= hi
                           for lo, hi in recipe['body_windows_mjd_tdb'][str(body)]):
                        for observer in (0, 10):
                            queries.add((body, t, observer))
    finally:
        spice.dafcls(handle)
    queries = sorted(queries)

    def evaluate(kernels):
        spice.kclear()
        try:
            for path in kernels:
                spice.furnsh(str(path))
            # Missing data is an error, never an omitted comparison.
            return [spice.spkez(body, epoch, 'J2000', 'NONE', observer)[0]
                    for body, epoch, observer in queries]
        finally:
            spice.kclear()

    original, compact = evaluate(paths), evaluate([args.kernel])
    mismatches = sum(not np.array_equal(a, b) for a, b in zip(original, compact))
    print(json.dumps({'queries': len(queries), 'state_mismatches': mismatches,
                      'all_states_bit_equal': mismatches == 0,
                      'kernel_sha256': sha256(args.kernel),
                      'spiceypy': spice.__version__, 'cspice': spice.tkvrsn('TOOLKIT')}, indent=2))
    return int(mismatches != 0)


if __name__ == '__main__':
    raise SystemExit(main())
