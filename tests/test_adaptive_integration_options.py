from types import SimpleNamespace
from pathlib import Path

import pytest

from adaptive_integration_options import (
    INTEGRATOR_TYPE_CHOICES,
    integrator_type_to_selection,
    selection_to_integrator_type,
    validate_adaptive_options,
)


def make_options():
    return SimpleNamespace(
        integratorType=0,
        integrator_tolerance=1.0e-8,
        integrator_error_control_mode=1,
        integrator_relative_tolerance=1.0e-8,
        integrator_absolute_tolerance_position=1.0e-6,
        integrator_absolute_tolerance_velocity=1.0e-9,
        integrator_absolute_tolerance_mass=1.0e-9,
        integrator_absolute_tolerance_time=1.0e-6,
        integrator_absolute_tolerance_other=1.0e-10,
        integrator_stm_error_control=1,
        integrator_stm_relative_tolerance=1.0e-8,
        integrator_stm_absolute_tolerance=1.0e-10,
        integration_time_step_size=86400.0,
        integrator_initial_step_size=0.0,
        integrator_minimum_step_size=0.0,
        integrator_safety_factor=0.9,
        integrator_minimum_step_scale=0.2,
        integrator_maximum_step_scale=5.0,
        integrator_rejection_limit=50,
    )


def test_gui_mapping_matches_backend_enum_without_sentinel_indices():
    assert INTEGRATOR_TYPE_CHOICES[0].startswith('rk7813M adaptive')
    assert INTEGRATOR_TYPE_CHOICES[1] == 'rk8 fixed step'
    assert [integrator_type_to_selection(value) for value in (0, 1)] == [0, 1]
    assert [selection_to_integrator_type(value) for value in (0, 1)] == [0, 1]


def test_generated_mission_options_does_not_warn_that_adaptive_is_unsupported():
    generated = (Path(__file__).resolve().parents[1] / 'PyEMTG' / 'MissionOptions.py').read_text(
        encoding='utf-8'
    )
    assert 'unsupported integrator type' not in generated


def test_adaptive_validation_accepts_documented_zero_step_sentinels():
    validate_adaptive_options(make_options())


@pytest.mark.parametrize(
    'attribute,value',
    [
        ('integrator_relative_tolerance', 0.0),
        ('integrator_absolute_tolerance_position', float('nan')),
        ('integrator_minimum_step_size', -1.0),
        ('integrator_safety_factor', 1.1),
        ('integrator_minimum_step_scale', 1.1),
        ('integrator_maximum_step_scale', 0.9),
        ('integrator_rejection_limit', 0),
    ],
)
def test_adaptive_validation_rejects_invalid_controller_contract(attribute, value):
    options = make_options()
    setattr(options, attribute, value)
    with pytest.raises(ValueError):
        validate_adaptive_options(options)
