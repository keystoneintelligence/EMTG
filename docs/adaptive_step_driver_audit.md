# Adaptive Step Driver Audit

## Summary

The adaptive RK error estimate is usable, but the integrated adaptive-step driver had two driver-level defects that made it unsafe to rely on for full propagation workflows:

- Backward propagation used sign-sensitive step control that assumed positive spans.
- Propagation history was recorded after `state_left` had already been overwritten, so accepted-step history collapsed to zero.

This branch fixes those issues in `IntegratedAdaptiveStepPropagator` and adds integrated-driver tests that exercise backward propagation, STM vs no-STM propagation, short-span capping, propagation history, and fixed/adaptive A/B behavior.

## Driver Fixes

- Treat `PropagationStepSize` as a positive magnitude and apply the sign from `propagation_span`.
- Cap the first and final accepted substeps by absolute remaining span so backward propagation lands exactly on the requested target.
- Reject/resize adaptive trial steps before accepting the state.
- Record propagation history before overwriting `state_left`.
- Advance `current_independent_variable` by the accepted signed step.
- Refresh `current_epoch` from the epoch state entry when present, with a signed-step fallback otherwise.
- Reject non-positive adaptive step sizes with a runtime error.

## Test Coverage

The integration test target now links the integrated propagator implementations and covers:

- Direct RK error-control behavior.
- Full `IntegratedAdaptiveStepPropagator` backward propagation without STM.
- Short-span adaptive propagation with an initial step larger than the span.
- Adaptive STM vs no-STM terminal-state consistency.
- Adaptive STM accuracy against an analytic STM.
- Fixed vs adaptive integrated-propagator A/B output.
- A legacy-driver simulation showing the old backward-span/history behavior.

Verification run:

```text
ctest --preset ci-fast --output-on-failure
100% tests passed, 0 tests failed out of 4
```

## Propagator Microbenchmark

Analytic driver case, span 10, initial/max step 2, tolerance 1e-9:

| Case | Mean Runtime | Terminal Error | Accepted Steps | RHS Evaluations |
| --- | ---: | ---: | ---: | ---: |
| adaptive forward no STM | 2.165 ms | 5.911e-13 | 83 | 1755 |
| adaptive backward no STM | 2.148 ms | 5.911e-13 | 83 | 1755 |
| adaptive forward STM | 17.623 ms | 5.911e-13 | 83 | 1755 |
| fixed step 2.0 | 0.076 ms | 5.484e-2 | 5 | 65 |
| fixed step 0.5 | 0.284 ms | 1.275e-7 | 20 | 260 |
| fixed step 0.1 | 1.435 ms | 5.862e-14 | 101 | 1313 |

Interpretation: adaptive is much more accurate than coarse fixed-step integration, but it is not automatically faster than a well-chosen fixed step for this analytic driver.

## Full-Trajectory A/B Benchmark

Benchmark case:

- Source options: `_local/asteroid_survey_trade_studies/cases/A20136163_NEXTC_FBLT.emtgopt`
- Propagator: integrated propagator
- Fixed option: `integratorType 1`
- Adaptive option: `integratorType 0`
- `integration_time_step_size 86400`
- `integrator_tolerance 1e-8`
- `seed_MBH 1`
- `MBH_RNG_seed 123456789`
- 5 paired repeats, alternating fixed/adaptive order

Runtime result:

| Mode | Mean | Std Dev | Min | Max |
| --- | ---: | ---: | ---: | ---: |
| fixed step | 130.203 s | 0.351 s | 129.873 s | 130.611 s |
| adaptive step | 121.888 s | 0.149 s | 121.741 s | 122.107 s |

Paired result:

- Adaptive was faster by 8.315 s on average.
- Mean wall-clock reduction: 6.386%.
- 95% CI half-width: 0.496 s, or 0.361 percentage points.

Solution-quality caveat:

- Fixed repeated to `J = -0.861303`, infeasibility `7.78194e-4`.
- Adaptive varied from `J = -0.845576` to `J = -0.574685`, infeasibility `1.19712e-2` to `4.56411e-2`.
- All runs exited with code 0 and completed, but emitted `FAILURE_*.emtg` files.

## Recommendation

Keep fixed-step integration as the standard trajectory-optimization path for now. The fixed driver was more stable for this NLP solve. Adaptive is now safer and better covered, and it is useful for validation, post-solve propagation, ephemeris checks, and future experiments, but it should not become the default optimizer path until convergence behavior is shown to be comparable on successful full-trajectory solves.
