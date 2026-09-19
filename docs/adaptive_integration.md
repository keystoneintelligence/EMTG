# Adaptive integration

EMTG supports the existing RK8 fixed-step path and an optional RK8(7) adaptive path. Fixed step remains the default. Adaptive integration is still under trajectory-optimization qualification and should be selected explicitly with:

```text
integratorType 0
```

`integratorType 1` selects the fixed-step default.

## Local error contract

Explicit component control (`integrator_error_control_mode 1`) normalizes the embedded error for state component `i` as

```text
|e_i| / (atol_i + rtol * max(|y_left_i|, |y_trial_i|)).
```

The controller accepts a trial when the infinity norm is at most one. This is a local embedded-error contract, not a promise that terminal/global error equals the requested tolerance. Position, velocity, mass/virtual propellant, epoch, and auxiliary states have separate absolute tolerances in their native EMTG units.

Legacy files remain deterministic with `integrator_error_control_mode 0` (the default): `integrator_tolerance` becomes the global relative tolerance and derives dimensional absolute tolerances from the universe length/time units and maximum spacecraft mass. Fixed-step files do not use any adaptive tolerance field.

## STM policy

`integrator_stm_error_control 1` computes a separate STM infinity norm and combines it with the state norm using `max(state_norm, STM_norm)`. Each STM absolute tolerance is the configured base value multiplied by the characteristic output/input unit ratio. This prevents position, velocity, mass, epoch, control, and propagation-variable sensitivities from being compared as raw dimensionally incompatible numbers.

`integrator_stm_error_control 0` makes state error alone control the step. The STM is still propagated and should be independently checked when this policy is used.

## Controller settings

- `integration_time_step_size` is the adaptive maximum step and the fixed step.
- `integrator_initial_step_size` is the adaptive initial step; zero selects the maximum step.
- `integrator_minimum_step_size` is the adaptive minimum; zero derives a floor from independent-variable scale and machine precision.
- `integrator_safety_factor`, `integrator_minimum_step_scale`, and `integrator_maximum_step_scale` bound controller resizing.
- `integrator_rejection_limit` prevents an infinite rejection loop.

All required tolerances must be finite and strictly positive. State, STM, error-estimate, and proposed-step NaN/Inf values fail with an exception rather than being silently accepted.

## Statistics

Both integrated propagators expose `getIntegrationStatistics()`. A propagation call resets its counters and records attempted/accepted/rejected steps, RHS and STM/RHS evaluations, accepted-step extrema and mean inputs, normalized error extrema, endpoint caps, dense requested epochs, event locations, failure counts, propagation span, and wall time. No hot-loop output is enabled.

## Dense output and events

Adaptive requested-epoch output uses cubic Hermite interpolation from accepted state endpoints and freshly evaluated endpoint derivatives. It is fourth-order interpolation, not an eighth-order continuous extension. Exact step endpoints are copied without interpolation. Dense STM output is not supported and is rejected explicitly.

The scalar event interface supports bracketing, direction filters (`-1`, `0`, `+1`), bisection to an independent-variable tolerance, and terminal or non-terminal observational events. A detected event resets the next controller proposal. Events must be continuous and observational: callers must end the propagation call at any force, control, eclipse, phase, or other model discontinuity. EMTG phase/control boundaries already issue separate propagation calls, so dense output never intentionally crosses those known boundaries.

## Qualification commands

```powershell
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast --output-on-failure
python -m pytest -q -p no:cacheprovider

cmake --preset ipopt-only
cmake --build --preset ipopt-only
ctest --preset ipopt-only --output-on-failure

python testatron/adaptive_ab_campaign.py --tier smoke
python testatron/adaptive_ab_campaign.py --tier local
python testatron/adaptive_ab_campaign.py --tier nightly --nightly-seed-count 30
```

The A/B runner holds the source case, initial guess, solver configuration, seed, budgets, universe, hardware models, and executable fixed inside each pair. It changes only `integratorType` and the required output directory, alternates run order across seeds, and writes checkpointed JSON/CSV. Its solution-family signature bins encounter epochs/altitudes, boundary states, mass, flight time, and objective before hashing; this is a deterministic screening classifier, not proof that two trajectories are topologically equivalent.
