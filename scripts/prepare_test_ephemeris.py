"""Stage the small, checksum-verified native-test universe using only Python."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/ephemeris'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(output, *, fixture=FIXTURE, universe=ROOT / 'testatron/universe'):
    output = output.resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; existing universes are never overwritten or mixed')
    manifest = json.loads((fixture / 'manifest.json').read_text(encoding='utf8'))
    archive = fixture / manifest['archive']['name']
    if sha256(archive) != manifest['archive']['sha256']:
        raise ValueError('Compressed test ephemeris checksum mismatch')
    # Decompress and verify before creating the requested universe. Source
    # mission data, production BSPs and packaged runtime data remain untouched.
    with tempfile.TemporaryDirectory(prefix='emtg-spk-') as temporary:
        kernel = Path(temporary) / manifest['kernel']['name']
        with gzip.open(archive, 'rb') as source, kernel.open('wb') as target:
            shutil.copyfileobj(source, target)
        if kernel.stat().st_size != manifest['kernel']['bytes'] or sha256(kernel) != manifest['kernel']['sha256']:
            raise ValueError('Expanded test ephemeris checksum mismatch')
        shutil.copytree(universe, output, ignore=shutil.ignore_patterns('*.bsp', '__pycache__'))
        destination = output / 'ephemeris_files' / kernel.name
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(kernel, destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='NEW short directory for the regression universe')
    args = parser.parse_args()
    try:
        manifest = prepare(args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f'Test universe: {args.output.resolve()}')
    print(f'Kernel SHA256: {manifest["kernel"]["sha256"]}')
    print('Set EMTG_TEST_UNIVERSE to this directory when running native regression tests.')
    print('This bounded fixture is not a general mission ephemeris.')


if __name__ == '__main__':
    main()
