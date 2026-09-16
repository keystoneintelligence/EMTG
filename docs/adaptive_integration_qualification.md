# Adaptive integration qualification report

## Decision

Adaptive integration is **experimental but generally optional** for explicit A/B work. Fixed-step RK8 remains the default and is still the qualified trajectory-optimization path. The implementation now has a defensible local-error contract, STM scaling, diagnostics, dense state output, observational events, corrected propagation-variable derivative handling, PyEMTG exposure, and reproducible campaign tooling. It does not yet have enough multi-transcription, multi-seed evidence to be considered production-qualified or eligible as the default.

## Scope and inherited implementation

This work matured EMTG's existing Dormand-Prince-style 13-stage RK8(7) implementation; it did not replace it. Commit `d08b372b` previously corrected backward propagation, endpoint acceptance/capping, epoch advancement, and history recording. The pre-change audit reported one FBLT pair in which adaptive propagation was 6.386% faster but produced less feasible, less repeatable optimization outcomes. The present work retained that warning and traced mission options through `PropagatorFactory`, the integrated driver, `ExplicitRungeKutta`, STM propagation, phase match-point derivatives, and mission output paths.

The trace found two additional inherited contract defects:

1. Factory error scaling encoded a single assumed layout although EMTG constructs 9- and 10-state integrations with 10-, 11-, and 14-dimensional STMs. Entries outside the assumed layout could be mis-scaled or zero.
2. The adaptive driver could pass an uninitialized/stale propagation-variable step derivative to every substep. The fixed driver correctly sets interior-step derivatives to zero and applies the signed boundary derivative only to the landing step.

Both defects are corrected.

## Algorithms and configuration

### State local error

For explicit component error mode, embedded component error is normalized as

```text
E_i = |e_i| / (atol_i + rtol max(|y_left,i|, |y_trial,i|))
E_state = max_i E_i.
```

The acceptance condition is `E <= 1`. The supported absolute-tolerance classes are Cartesian position (km), velocity (km/s), mass and virtual propellant (kg), epoch/independent variable (s for time-domain propagation), and formulation-specific auxiliary state. All required values must be finite and strictly positive.

`integrator_error_control_mode 0` is the default compatibility migration. It uses legacy `integrator_tolerance` as `rtol` and derives each `atol` by multiplying that value by its universe/mass characteristic scale. `integrator_error_control_mode 1` uses the explicit per-class fields documented in `docs/adaptive_integration.md`. Fixed-step behavior does not inspect these fields.

This is local embedded-error control. The tests measure global terminal error separately and do not claim that the configured tolerance is a terminal-error bound.

### STM local error

The default policy computes a separate normalized STM infinity norm. For STM entry `(i,j)`, the absolute tolerance is

```text
stm_atol_ij = stm_atol_base characteristic_scale_i / characteristic_scale_j.
```

The characteristic coordinates cover position, velocity, mass, epoch, virtual tanks, dimensionless controls, and the time-like propagation-variable column. The combined controller error is `max(E_state, E_STM)`. A state-only policy remains selectable, but it does not waive independent STM qualification.

Portable tests cover state-only versus STM-aware control, strict STM dominance, identity/near-zero entries through the relative-plus-absolute denominator, analytic oscillator STM propagation, and a separate propagation-variable column. A 10-unit propagation produced maximum STM error `4.441e-15` in the analytic fixture.

### Step controller and failures

`integration_time_step_size` is now enforced as the adaptive maximum. Initial and minimum steps, safety factor, minimum/maximum resize factors, and consecutive rejection limit are configurable. The default resize exponent is `-1/8`, appropriate to the embedded local-error estimate. A zero minimum selects

```text
64 epsilon max(1, |independent variable|, |propagation span|),
```

rather than an absolute hard-coded threshold. State, STM, error-estimate, span, and proposed-step NaN/Inf values fail explicitly. Forward and backward propagation use the same magnitude controller with a signed direction.

### Derivatives and schedule behavior

Interior adaptive steps now use zero propagation-variable step partial; only the exact landing step uses the signed boundary partial. The accepted schedule itself is intentionally stripped from AD, matching the standard interpretation that the numerical mesh is an algorithmic choice rather than a physical variable.

The analytic continuity sweep perturbed a 10-unit duration while propagating a distinct duration column in the STM:

| Perturbation | Directional FD | Propagated derivative | Absolute difference | Accepted steps (-/0/+) | Rejections (-/0/+) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| `1e-3` | 0.1676492889 | 0.1676492989 | `1.006e-8` | 195/195/195 | 14/14/14 |
| `1e-4` | 0.1676492988 | 0.1676492989 | `1.015e-10` | 195/195/195 | 14/14/14 |
| `1e-5` | 0.1676492989 | 0.1676492989 | `3.804e-12` | 195/195/195 | 14/14/14 |

Repeated center evaluations produced identical terminal state, STM derivative, and accepted-step schedule. No step-pattern transition occurred in this local window. This is encouraging smoke evidence, not a complete map of transition surfaces in decision-variable space; no frozen-mesh mode was introduced.

### Statistics

`IntegrationStatistics` is queryable from both integrated drivers and resets on every propagation call. It records attempted, accepted, and rejected steps; RHS and STM/RHS evaluations; accepted-step minimum, maximum, sum/mean; maximum and final normalized errors; endpoint caps; requested-epoch evaluations; event locations; underflow/rejection-limit failures; span; and wall time. Instrumentation is counters and arithmetic only, with no unconditional output or hot-loop allocation. Tests assert counter identities for rejection and STM cases. Mission-wide aggregation/output remains future work.

### Dense output and events

The current tableau does not include a checked-in eighth-order continuous extension. Dense state output therefore uses cubic Hermite interpolation with accepted endpoint states and two freshly evaluated endpoint derivatives. It is documented and tested as fourth-order interpolation. Exact endpoints are copied. A high-frequency analytic fixture gave maximum dense error `1.94003e-1` at maximum step 1.0 and `4.45965e-2` at maximum step 0.5, demonstrating the expected convergence trend without claiming eighth order.

Dense STM output is explicitly rejected. Scalar continuous observational events support bracketing, direction filters, bisection to a configured tolerance, terminal/non-terminal results, and controller reset. The analytic downward zero crossing was located within `2e-7` time units. Events may not represent a force, control, eclipse, phase, or other discontinuity; callers must end/restart propagation there. Existing EMTG phase and control segments already call the propagator separately.

## User and options exposure

Generated C++ and Python option sources were regenerated from `OptionsOverhaul/list_of_missionoptions.csv`. PyEMTG now displays the backend enum in the correct order: adaptive `0`, fixed `1`. It exposes both choices, keeps fixed selected by existing/default files, shows adaptive-only fields conditionally, validates numeric inputs, and labels adaptive mode experimental. Round-trip tests cover both modes and every new field.

## Executed verification

Baseline before edits:

```text
ctest --preset ci-fast --output-on-failure     4/4 passed
python -m pytest -q -p no:cacheprovider        34 passed
```

Final verification:

```text
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast --output-on-failure     4/4 passed
python -m pytest -q -p no:cacheprovider        47 passed

cmake --preset ipopt-only
cmake --build --preset ipopt-only              293 build steps completed
ctest --preset ipopt-only --output-on-failure  5/5 passed
```

The Release build used MSVC 19.44.35214.0 and IPOPT 3.14.20 on Windows 10.0.19045, AMD64 Family 23 Model 113 Stepping 0. The plain PowerShell baseline build initially lacked MSVC standard-library include variables; executing inside `vcvars64.bat` resolved this environment issue.

Portable numerical evidence:

| Case | Result |
| --- | ---: |
| Embedded single-step error fixture | `5.906531e-3` (nonzero) |
| Oscillator, legacy local tolerance `1e-5` | terminal error `9.218e-10`, 25 accepted, 18 rejected |
| Oscillator, legacy local tolerance `1e-9` | terminal error `5.911e-13`, 83 accepted, 52 rejected |
| Adaptive forward, span 10 | terminal error `1.184e-12`, 87 accepted, 1404 RHS calls |
| Adaptive backward, span -10 | terminal error `1.184e-12`, endpoint error `1.776e-15` |
| Adaptive short span 0.25 with initial/max 2 | one capped step, endpoint error `2.776e-17` |
| Adaptive STM analytic fixture | maximum STM error `4.441e-15` |

These cover analytic forcing/harmonic motion, forward/backward and short spans, rejection, STM/no-STM consistency, dense output, scalar roots, fixed/adaptive propagation, invalid inputs, counters, determinism, and directional derivatives. They do not yet cover a dedicated two-body tolerance sweep, mass-changing thrust reference, high-eccentricity reference, or independently generated SRP/third-body reference.

## Full-trajectory paired smoke result

Command:

```text
python testatron/adaptive_ab_campaign.py --tier smoke --emtg _local/builds/ipopt-only/src/EMTGv9.exe --timeout 600 --output-root _local/adaptive_ab_qualification
```

Case SHA-256 `ec1c5853...e73f4ec`, executable SHA-256 `243e1b2c...4ee3bb6`, MBH seed `104729`, order fixed then adaptive. The same source options, initial guess, IPOPT settings, universe/kernels, hardware models, executable, and per-run budget were used. The pair guard rejects any difference other than `integratorType` and the required output directory.

| Metric | Fixed | Adaptive |
| --- | ---: | ---: |
| Feasible output | yes | yes |
| Wall time | 298.068 s | 156.401 s |
| Time to first feasible | 289.688 s | 145.509 s |
| Objective | -0.7624281481 | -0.7634110936 |
| Worst constraint violation | `4.6840e-6` | `9.2360e-6` |
| Final mass | 1906.070 kg | 1908.528 kg |
| Flight time | 5.73685 years | 6.57694 years |
| Family signature | `de35b163a721346c` | `3094db90451b21d0` |

Adaptive was 47.528% faster in this one pair and found a slightly better objective with a higher but still feasible reported violation. The coarse deterministic family classifier places the two results in different families, driven in part by an approximately 0.84-year flight-time difference and different encounter/boundary features. Thus this smoke pair does not show adaptive collapsing onto the fixed solution. One seed cannot estimate convergence probability, objective distributions, or family-loss probability; those gates remain open. The reviewed machine-readable summary is `docs/benchmarks/adaptive_smoke_20260710.json`; complete run artifacts remain under `_local` and are intentionally uncommitted.

## Five-seed equal-budget local campaign

Command:

```text
python testatron/adaptive_ab_campaign.py --tier local --case testatron/tests/integration_asteroid_missions/A20136163_AEPS_IPOPT_FBLT.emtgopt --emtg _local/builds/ipopt-only/src/EMTGv9.exe --run-budget-seconds 180 --timeout 240 --output-root _local/adaptive_ab_qualification
```

Five paired seeds were run with fixed/adaptive order alternated and the same 180-second EMTG budget in each mode. EMTG output/cleanup makes observed wall time slightly longer than the internal budget.

| Metric | Fixed | Adaptive |
| --- | ---: | ---: |
| Feasible runs | 0/5 | 5/5 |
| Median wall time | 193.527 s | 157.549 s |
| Runtime standard deviation | 0.234 s | 0.420 s |
| Repeated objective | -0.7631260446 | -0.7634110936 |
| Repeated worst violation | `1.51895e-4` | `9.235995e-6` |
| Median time to first feasible | not reached | 146.633 s |
| Distinct feasible family signatures | 0 | 1 (`3094db90451b21d0`) |

Adaptive reduced paired wall time by 18.617% on average (sample standard deviation 0.273 percentage points; 95% t-interval half-width 0.339 percentage points) and was the only mode to reach feasible output under the equal budget. All five seeds repeated the same outcome in each mode, which indicates that this budget was dominated by a deterministic first solve rather than broad MBH basin exploration. The result is strong evidence against a convergence regression for this FBLT/IPOPT fixture, but it cannot establish general stochastic convergence probability or family diversity. The reviewed artifact is `docs/benchmarks/adaptive_local_20260710.json`.

## Transcription readiness

| Transcription/path | Integrated adaptive path traced | Executed optimization evidence | Readiness |
| --- | --- | --- | --- |
| FBLT | thrust segments, forced coasts, forward/backward match point, STM 14 | one unconstrained-budget pair plus five equal-180-second pairs | experimental optional |
| PSFB | thrust substeps/coasts, STM 14 | paired fixture attempted; both modes rejected identical invalid epoch bounds before propagation | not exercised/not qualified |
| PSBI | integrated substeps/coasts | paired fixture attempted; both modes rejected identical invalid epoch bounds before propagation | not exercised/not qualified |
| Integrated CoastPhase | forward/backward halves and output propagation | analytic driver only | not qualified |
| ControlLawThrustPhase | integrated forward/backward halves, STM 11 | traced only | not qualified |
| MGAnDSMs | integrated coast subphases, STM 11 | traced only | not qualified |
| Probe entry | 9 states, STM 10 | traced/layout-supported only | not qualified |
| MGALT | Kepler/impulse propagation; adaptive integrated RK is not the primary transcription path | not applicable to primary steps | not applicable/limited |
| Sundman coast | separate independent-variable semantics | traced only | disabled from qualification claim |

## Qualification gates

- **Correctness:** portable/fixed tests, endpoint, backward, rejection, STM, dense/event, invalid-value, and deterministic-repeat tests pass.
- **Accuracy:** analytic oscillator/STM and convergence trends pass; astrodynamics reference breadth is incomplete.
- **Derivatives:** the corrected duration column agrees with local finite differences and the FBLT smoke solve converges; step-transition regions and other decision variables remain under-characterized.
- **Convergence:** the unconstrained-budget FBLT pair was feasible in both modes; the equal-180-second local tier was adaptive 5/5 versus fixed 0/5. Cross-transcription and nightly-scale non-regression are unproven.
- **Novelty:** the unconstrained pair produced distinct reproducible family signatures, and the local tier retained one adaptive feasible family when fixed found none. Population-level diversity remains unproven because all seeds repeated deterministic first-solve outcomes.
- **Runtime:** propagation microtests and one full pair are reported separately. Dense overhead and mission-wide RHS totals are not yet benchmarked.
- **Compatibility:** fixed step remains default; generated options and PyEMTG round-trip tests pass.

## Remaining risks and required next evidence

1. Run the local and nightly campaign tiers across FBLT, PSFB, PSBI, integrated coast, ControlLawThrustPhase, multiple-flyby, perturbation, and probe-entry cases with enough paired seeds for confidence intervals.
2. Add independent two-body/high-eccentricity, mass-changing thrust, SRP/third-body, and astrodynamics STM references with tolerance/resolution sweeps and JSON tables.
3. Aggregate propagator statistics through mission evaluation so full campaigns report RHS/STM calls and accepted/rejected steps, not only wall time.
4. Sweep real NLP decision variables around accepted-step schedule transitions and compare objective/constraint/Jacobian continuity to fixed step.
5. Either add and verify a tableau-consistent high-order continuous extension or keep cubic Hermite's documented accuracy/cost limitation.
6. Add discontinuity-aware event callbacks before using events to alter controls or force models; the present interface is observational only.
7. Provision licensed/large runtime assets for nightly CI without committing kernels; until then, full campaigns are reproducible local/manual jobs while fast controller/options coverage remains PR-required.

Compact paired PSFB and PSBI smoke commands were also executed with equal 30-second budgets. Both fixed and adaptive variants stopped in IPOPT setup with the same incompatible launch-epoch bounds before any propagation occurred, so these runs are recorded as `invalid_bounds_before_propagation` and are not counted as integrator convergence evidence. The fixtures must be repaired or supplied with the intended legacy bounds before those transcription gates can be evaluated.

## Recommendation

Keep adaptive integration explicitly selectable and experimental. It is suitable for controlled A/B experiments, validation propagation, and post-processing where its local-error/statistics contract is useful. Do not change the default and do not enable it broadly by transcription until multi-seed convergence, derivative-transition, and solution-diversity gates are met.
