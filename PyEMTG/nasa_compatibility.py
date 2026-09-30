"""Explicit option export for the pinned NASA EMTG vocabulary.

This is an option-format bridge, not a claim of solver/numerical equivalence.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
from pathlib import Path


class NASACompatibilityError(ValueError):
    """An option cannot be represented by the supported NASA release."""


def export_nasa_options(source: str | Path, destination: str | Path, *, solver: str) -> dict:
    """Export an options file using NASA names and an explicit SNOPT choice.

    Nondefault fork-only controls and unknown options fail before writing.
    Inactive fork defaults are omitted and listed in the returned report.
    Native constraints, trial vectors, and physical tolerances are preserved.
    SNOPT installation and scientific qualification remain separate steps.
    """
    if solver.upper() != 'SNOPT':
        raise NASACompatibilityError('NASA export requires explicit solver="SNOPT"; IPOPT is not supported by this NASA revision')
    vocabulary = json.loads(Path(__file__).with_name('nasa_options.json').read_text(encoding='utf8'))
    scope = 'mission'
    block_end = None
    blocks = {'BEGIN_MANEUVER_CONSTRAINT_BLOCK', 'BEGIN_BOUNDARY_CONSTRAINT_BLOCK',
              'BEGIN_PHASE_DISTANCE_CONSTRAINT_BLOCK', 'BEGIN_TRIALX'}
    lines = ['# NASA-compatible EMTG options; solver explicitly selected by exporter', 'NLP_solver_type 0']
    omitted = []
    seen = set()
    for number, raw in enumerate(Path(source).read_text(encoding='utf8').splitlines(), 1):
        stripped = raw.strip()
        if block_end:
            lines.append(raw)
            if stripped == block_end:
                block_end = None
            elif stripped.startswith(('BEGIN_', 'END_')):
                raise NASACompatibilityError(f'line {number}: unexpected marker inside block: {stripped}')
            continue
        if not stripped or stripped.startswith('#'):
            # Option comments may describe fork-only controls. Keep the output
            # vocabulary unambiguous by retaining only scientific block comments.
            continue
        if stripped == 'BEGIN_JOURNEY':
            if scope != 'mission':
                raise NASACompatibilityError(f'line {number}: nested journey')
            scope = 'journey'
            seen = set()
            lines.append(stripped)
            continue
        if stripped == 'END_JOURNEY':
            if scope != 'journey':
                raise NASACompatibilityError(f'line {number}: unmatched END_JOURNEY')
            scope = 'mission'
            lines.append(stripped)
            continue
        if stripped in blocks and scope == 'journey':
            block_end = stripped.replace('BEGIN_', 'END_', 1)
            lines.append(stripped)
            continue
        parts = stripped.split(None, 1)
        key, value = parts[0], parts[1] if len(parts) > 1 else ''
        definitions = vocabulary[scope]
        target = definitions['renames'].get(key, key)
        if target in seen:
            raise NASACompatibilityError(f'line {number}: duplicate option {target}')
        seen.add(target)
        if key == 'NLP_solver_type' and scope == 'mission':
            continue
        if key in definitions['fork_only_defaults']:
            default = definitions['fork_only_defaults'][key]
            try:
                inactive = Decimal(value.strip()) == Decimal(default)
            except InvalidOperation:
                inactive = value.strip() == default
            if not inactive:
                raise NASACompatibilityError(f'line {number}: unsupported fork-only setting {key}={value}')
            omitted.append(key)
            continue
        if target not in definitions['names'] and not (scope == 'mission' and target == 'user_data'):
            raise NASACompatibilityError(f'line {number}: unsupported {scope} option {key}')
        lines.append(target + ' ' + value)
    if block_end or scope != 'mission':
        raise NASACompatibilityError('unterminated journey or constraint/trial block')
    Path(destination).write_text('\n'.join(lines) + '\n', encoding='utf8')
    return {'nasa_revision': vocabulary['nasa_revision'], 'solver': 'SNOPT',
            'omitted_inactive_fork_defaults': sorted(set(omitted))}
