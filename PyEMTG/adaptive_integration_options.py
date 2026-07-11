"""Pure-Python mapping and validation helpers for PyEMTG integration controls."""

import math


INTEGRATOR_TYPE_CHOICES = (
    "rk7813M adaptive step (experimental)",
    "rk8 fixed step",
)

ERROR_CONTROL_MODE_CHOICES = (
    "Legacy tolerance migration",
    "Explicit component atol/rtol",
)

STM_ERROR_CONTROL_CHOICES = (
    "State only",
    "State and STM",
)


def integrator_type_to_selection(integrator_type):
    value = int(integrator_type)
    if value not in (0, 1):
        raise ValueError("EMTG integrator type must be 0 (adaptive) or 1 (fixed).")
    return value


def selection_to_integrator_type(selection):
    return integrator_type_to_selection(selection)


def validate_adaptive_options(options):
    """Validate values used only by the adaptive driver.

    A zero initial/minimum step is a documented sentinel selecting the maximum
    step or the scale-aware automatic floor, respectively.
    """

    positive = (
        "integrator_tolerance",
        "integrator_relative_tolerance",
        "integrator_absolute_tolerance_position",
        "integrator_absolute_tolerance_velocity",
        "integrator_absolute_tolerance_mass",
        "integrator_absolute_tolerance_time",
        "integrator_absolute_tolerance_other",
        "integrator_stm_relative_tolerance",
        "integrator_stm_absolute_tolerance",
        "integration_time_step_size",
        "integrator_safety_factor",
        "integrator_minimum_step_scale",
        "integrator_maximum_step_scale",
    )
    for name in positive:
        value = float(getattr(options, name))
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{name} must be finite and strictly positive.")

    for name in ("integrator_initial_step_size", "integrator_minimum_step_size"):
        value = float(getattr(options, name))
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(f"{name} must be finite and non-negative.")

    if float(options.integrator_safety_factor) > 1.0:
        raise ValueError("integrator_safety_factor must not exceed 1.")
    if float(options.integrator_minimum_step_scale) > 1.0:
        raise ValueError("integrator_minimum_step_scale must not exceed 1.")
    if float(options.integrator_maximum_step_scale) < 1.0:
        raise ValueError("integrator_maximum_step_scale must be at least 1.")
    if int(options.integrator_rejection_limit) <= 0:
        raise ValueError("integrator_rejection_limit must be positive.")

    integrator_type_to_selection(options.integratorType)
    if int(options.integrator_error_control_mode) not in (0, 1):
        raise ValueError("integrator_error_control_mode must be 0 or 1.")
    if int(options.integrator_stm_error_control) not in (0, 1):
        raise ValueError("integrator_stm_error_control must be 0 or 1.")
