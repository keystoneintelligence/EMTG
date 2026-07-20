# Research-to-runtime roadmap for EMTG

Date: 2026-07-09
Branch: `codex/research-to-runtime-roadmap`
Code baseline: `6a0a23b5adb46a40a647d4fb5d65f17c9053df47`

## Decision summary

The attached research report has the right main conclusion, but the local code changes the implementation picture in useful ways.

1. **MGALTS is the best new transcription to build.** It directly targets difficult high-eccentricity and many-revolution MGALT cases. The paper's strongest demonstrated benefit is better control-node placement and access to a better solution topology with the same segment count. Its raw propagation is also iteration-free, but the paper does not provide a wall-clock runtime benchmark, so that part should be treated as an expected secondary benefit rather than a promised speedup.
2. **MGALTS is not greenfield in this fork.** The phase enum, options vocabulary, build directories, conversion utilities, a working Sundman coast phase, controlled Sundman equations of motion, variational equations, and STM tests already exist. The missing pieces are the anomaly-domain analytic Kepler propagator and the actual bounded-impulse phase/Jacobian assembly.
3. **The fastest near-term runtime win is smaller than a new transcription.** Pareto MBH, analytic MGALT/FBLT derivatives, smooth thruster transitions, and spline ephemerides are already present. However, MBH's optional infeasible-start screening computes a full Jacobian before deciding to skip the NLP. A constraints-only screening path can make that feature materially cheaper, after which its threshold should be tuned empirically.
4. **The 2019 low-to-high-fidelity workflow is present but not productized.** Python code can convert MGALT solutions through high-fidelity flybys and into PSFB, but the entry point is a side-effect-heavy batch script with shell-string execution, special-case settings, and no focused regression suite. Turning this into a supported continuation command should reduce time-to-solution and analyst effort for high-fidelity problems even if it does not make individual propagations faster.
5. **FBLTS is the logical second transcription after MGALTS.** The controlled Sundman EOM and its first-order variational equations already exist, and the `FBLTS` enum/path is reserved. The likely benefit is better error distribution and fewer right-hand-side evaluations on eccentric integrated arcs. This is plausible and architecturally well aligned, but it was future work in the cited high-fidelity paper and has no published EMTG runtime result in the reviewed corpus.
6. **Q-Law is a good specialized extension, not a general EMTG accelerator.** It fits the existing control-law thrust and integrated-STM infrastructure moderately well. Its payoff is low-dimensional, gradient-based planetocentric spiral design and better spiral seeds for end-to-end problems. It should start as a seeder or dedicated phase, not be inserted into MGALT.
7. **Sundman DDP is valuable only for the extreme many-revolution regime.** It demonstrated trajectories of more than 1000 revolutions, but a DDP engine is a different optimization architecture. It fits best as an optional external/seeding subsystem. It should not delay MGALTS, continuation tooling, or Q-Law.
8. **Do not start an exact-Hessian project based on this corpus.** The Ellison/Englander bounded-impulse work supplies first-order Jacobians and STMs. The current IPOPT integration correctly uses limited-memory Hessians. There is no paper-derived closed-form EMTG Hessian ready to extract.

## Prioritized capability matrix

| Priority | Capability | Local status | Fit | How it can reduce time-to-solution | What we get | Evidence confidence |
|---|---|---:|---:|---|---|---:|
| P0 | Cheap MBH infeasible-start screening plus benchmarked MBH settings | Present but screening evaluates `needG=true`; feature default is off | Very high | Avoid expensive NLP starts and avoid computing derivatives for candidates that will be rejected | More hops and useful local solves per wall-clock budget | High for code path; benefit must be measured |
| P0 | Performance/robustness benchmark harness | Missing as a focused research benchmark | Very high | Prevents optimizing the wrong layer and makes transcription claims falsifiable | Wall time, evaluation time, NLP iterations, success rate, RHS calls, and quality curves | High |
| P1 | MGALTS: generalized-anomaly bounded-impulse phase with analytic Jacobian | Enum/path reserved; phase files empty; factory throws | High | Better node placement can use fewer controls and improve convergence/topology; analytic propagation removes per-segment Kepler root solves | Faster and more robust eccentric/many-revolution preliminary design | High for capability; medium for wall-clock magnitude |
| P1 | Supported MGALT/MGALTS -> FBLT/PSFB fidelity continuation | Core phases and legacy converters exist | High | Starts expensive integrated problems near a feasible basin instead of solving them cold | Lower high-fidelity convergence cost and less analyst intervention | High |
| P2 | FBLTS: Sundman-domain finite-burn phase with variational equations | Enum/path reserved; controlled Sundman EOM exists; phase is empty | Medium-high | Concentrates integration work where dynamics change fastest and may reduce fixed-step count | Better-conditioned high-fidelity eccentric arcs | Medium-low until benchmarked |
| P2 | Analytic Q-Law guidance/gain optimization | No Q-Law; fixed velocity/anti-velocity control-law phase exists | Medium | Replaces thousands of stochastic gain trials or many direct control variables with a small gradient-based gain NLP | Fast planetocentric escape/capture spirals and better hybrid seeds | High for specialized use; low for general missions |
| P3 | Sundman-transformed DDP seeder | Absent | Low in core, medium as adjunct | Handles very large many-revolution control histories efficiently | Specialized 1000+ revolution spiral capability and feedback-like seeds | High for research result; low for EMTG integration cost |
| P3 | Parallel moon-tour flyby-tree search | Not found in core/PyEMTG | Low unless moon-tour automation is a product goal | Parallelizes discrete sequence discovery before continuous optimization | Autonomous satellite-tour sequence generation | Medium; dissertation abstract is the main reviewed source |
| No new build | Analytic MGALT/MGAnDSMs Jacobians, smooth power/throttle derivatives, SplineEphem, Pareto MBH | Already implemented | Already integrated | Preserve, test, and tune | These are baseline capabilities, not current gaps | High |

## What is already in the fork

### Analytic bounded-impulse derivatives

The 2018 Jacobian architecture is visible directly in the current MGALT implementation:

- [`MGALTphase.cpp`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT/MGALTphase.cpp) builds forward/backward augmented STMs, maneuver transition matrices, cumulative chains, HPTMs, and sparse match-point derivatives.
- [`BoundedImpulseManeuver.cpp`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT/BoundedImpulseManeuver.cpp) computes the power, propulsion, time, and natural-perturbation derivative inputs used by the maneuver transition matrices.
- [`FBLTphase.cpp`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/FBLT/FBLTphase.cpp) propagates integrated STMs and control sensitivities for the finite-burn model.
- Normal builds use `double`; AD instrumentation can replace it with `GSAD::adouble` through [`doubleType.h`](../src/Core/doubleType.h), which is useful for derivative verification without paying AD overhead in production.

This means the first-order derivative program from the 2014 and 2018 papers is already a baseline dependency. Reimplementing those equations would not add capability.

### Smooth propulsion and ephemerides

The practical robustness features from the 2018 application paper are also present:

- [`ThrottleSetting.cpp`](../src/HardwareModels/ThrottleSetting.cpp) evaluates smooth Heaviside transitions and their derivatives.
- [`ElectricPropulsionSystem.cpp`](../src/HardwareModels/ElectricPropulsionSystem.cpp) carries those values into thrust, mass-flow, power, and command derivatives.
- Spline ephemerides are enabled by default in [`CMakeLists.txt`](../CMakeLists.txt) and `ephemeris_source` defaults to SplineEphem in [`missionoptions.cpp`](../src/Core/missionoptions.cpp).

The useful work here is validation and tuning: compare SplineEphem against SPICE for setup cost, evaluation cost, memory, and derivative smoothness, and expose a benchmark-backed sampling recommendation. It is not a missing implementation.

### Long-tailed MBH

The 2014 MBH result is already the default:

- `MBH_hop_distribution = 2` (Pareto) and `MBH_Pareto_alpha = 1.4` in [`missionoptions.cpp`](../src/Core/missionoptions.cpp).
- Uniform, Cauchy, Pareto, and Gaussian hops are all implemented in [`monotonic_basin_hopping.cpp`](../src/InnerLoop/monotonic_basin_hopping.cpp).

The remaining opportunity is to make the existing search cheaper and empirically tune it for representative mission families.

## P0: recover speed before adding a transcription

### Make MBH screening constraints-only

The screening flow at [`monotonic_basin_hopping.cpp:473`](../src/InnerLoop/monotonic_basin_hopping.cpp#L473) calls:

```cpp
myProblem->evaluate(X_after_hop_unscaled, F, G, true);
```

It then checks feasibility and may skip the NLP. When the candidate is skipped, every analytic Jacobian calculation in that evaluation was unnecessary. The first implementation experiment should be:

1. If `checkFeasibilityTolInMBHToSkipNLP` is enabled, evaluate the candidate with `needG=false` for screening.
2. If it passes, let the NLP request the derivatives it needs through the existing cached `NLP_interface` path.
3. If screening is disabled, determine whether the pre-evaluation can be removed entirely or retained as a function-only validation call.
4. Record function-only time, function-plus-Jacobian time, skipped candidates, NLP calls, and best objective versus wall time.

This change should be benchmarked separately for MGALT and FBLT because their derivative cost profiles are very different. It is a code-derived improvement rather than a new paper capability, but it is the quickest way to get more value from the already-implemented analytic derivatives and MBH research.

### Establish a research benchmark suite

A capability should not be called a runtime improvement until it passes a repeatable benchmark. Add a small manifest-driven suite with at least these classes:

| Case family | Why it is needed |
|---|---|
| Benign single-revolution Earth-Mars-like MGALT | Detect overhead/regression on the common case |
| High-eccentricity, multi-revolution heliocentric rendezvous | Primary MGALTS target |
| Near-parabolic and hyperbolic segment cases | Universal-variable and derivative edge regimes |
| Integrated FBLT with third-body/SRP/power derivatives | High-fidelity cost and STM accuracy |
| Planetocentric escape/capture spiral | Q-Law, FBLTS, and DDP target |
| Multi-flyby low-to-high-fidelity conversion | Continuation reliability and analyst-time target |

For each fixed seed and configuration, record:

- wall time to first feasible solution and to best solution;
- function-only and function-plus-Jacobian evaluation time;
- NLP major iterations and solver function/Jacobian requests;
- MBH hops, NLP launches, screening rejects, resets, and success rate across seeds;
- control segment count, decision-variable count, Jacobian nonzeros, and peak memory;
- integrated RHS calls and rejected/adaptive steps where applicable;
- final feasibility, objective, delivered mass, flight time, and trajectory family/topology.

The comparison must hold objective and feasibility tolerances constant. A faster answer at weaker feasibility is not a speedup.

## P1: MGALTS

### Capability

The [2017 MGALTS paper](https://ntrs.nasa.gov/citations/20170001426) makes total generalized anomaly `chi_p` an NLP decision variable and uses equal `delta chi = chi_p / N` segments. Each segment directly computes universal functions, elapsed time, Lagrange coefficients, state, and the derivatives needed by the sparse Jacobian. This does two different jobs:

1. It distributes control nodes geometrically, placing more control authority near periapsis on eccentric trajectories.
2. It removes the Laguerre/Newton root solve that time-domain universal-variable propagation performs to recover generalized anomaly.

The paper's 45P example is a solution-quality/topology result, not a runtime benchmark. With 200 segments, ordinary MGALT produced a 10.10-year solution and MGALTS a 7.96-year solution with final masses within about one percent. Raising ordinary MGALT to 400 segments recovered a more comparable topology. The defensible expected win is therefore fewer controls and better convergence on the target problem class; raw wall-clock gains must be measured locally.

### Local fit

Fit is high, with important caveats:

- `MGALTS` already exists in the phase enum and option serialization.
- The build already descends into the MGALTS directory.
- [`journey.cpp:111`](../src/Mission/Journey/journey.cpp#L111) explicitly reserves the factory case but throws “not yet implemented.”
- [`MGALTS_phase.cpp`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT/MGALTS/MGALTS_phase.cpp) and its header are zero-byte placeholders.
- [`MGALTphase.h`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT/MGALTphase.h) exposes virtual phase, propagation, match-point, distance-constraint, and delta-v hooks, showing that specialized phases were anticipated.
- The ordinary propagator still runs a Laguerre/Newton loop in [`KeplerPropagatorTimeDomain.cpp:95`](../src/Propagation/KeplerPropagatorTimeDomain.cpp#L95).
- [`SundmanCoastPhase`](../src/Mission/Journey/Phase/TwoPointShootingPhase/CoastPhase/SundmanCoastPhase/SundmanCoastPhase.cpp) already demonstrates an extra independent-variable decision, an epoch/time match constraint, sparse derivatives with respect to that variable, and forward/backward STM handling.

The existing Sundman EOM does **not** replace the missing MGALTS propagator. MGALTS needs a closed-form anomaly-domain two-body map; the current Sundman EOM is an integrated dynamics path.

### Required implementation slices

#### Slice A: anomaly-domain Kepler kernel

Create a standalone anomaly-domain propagator that:

- accepts initial Cartesian state and signed `delta chi`;
- handles elliptic, near-parabolic, and hyperbolic conics without iteration;
- returns propagated Cartesian state and `delta t`;
- returns the state STM, `d state / d chi`, `d delta t / d chi`, and `d delta t / d initial state`;
- has no spacecraft, phase, optimizer, or output dependencies.

Verify it against the existing time-domain propagator by using its computed `delta t` as the time-domain input, and verify derivatives with AD/complex-step instrumentation.

#### Slice B: one phase, no optional path constraints

Implement the minimum useful MGALTS phase:

- one `chi_p` decision variable per phase;
- equal signed anomaly segments for forward and backward halves;
- the paper's boundary-of-segment impulse ordering;
- elapsed segment time feeding thrust and mass-flow calculations;
- a time/epoch match defect in addition to position, velocity, mass, and tank defects;
- sparse derivatives with respect to controls, boundary states/times, phase flight time, and `chi_p`.

Do not initially add every MGALT optional constraint. First prove the core state/time/mass Jacobian and solve a simple high-eccentricity case.

#### Slice C: feature parity

Add, in order:

1. forced coasts and TCMs;
2. distance/path constraints;
3. all power/throttle modes and optional control command variables;
4. output, ephemeris, maneuver, and target specifications;
5. PyEMTG option/UI support and MGALT-to-MGALTS initial-guess conversion;
6. Testatron inventory and regression cases.

The phase should share bounded-impulse maneuver and Jacobian-chain code with MGALT. Copying the approximately 89 KB `MGALTphase.cpp` and editing it would produce fast initial progress but a permanent correctness burden. A small common bounded-impulse phase layer or propagation-domain strategy is the better long-term shape.

### Acceptance gates

- Equation/kernel tests cover elliptic, near-parabolic, and hyperbolic states in both propagation directions.
- Analytic derivatives match AD or complex-step to a documented scaled tolerance; finite differences are diagnostic only.
- MGALTS and MGALT agree on simple low-eccentricity cases within transcription error.
- A 45P-like high-eccentricity benchmark demonstrates the expected topology/node-placement benefit.
- At matched feasibility and solution quality, report the segment count, decision variables, Jacobian nonzeros, NLP iterations, evaluation cost, and total wall time. Do not claim a runtime win from segment count alone.
- Benign MGALT remains unchanged, and MGALTS does not regress its target benchmark by more than a predeclared tolerance.

## P1: make fidelity continuation a supported workflow

The [2019 high-fidelity multiple-shooting paper](https://ntrs.nasa.gov/citations/20190028884) describes converting an easier low-fidelity trajectory into a high-fidelity multiple-shooting problem. This fork already contains the core phase technology and a conversion pipeline:

- [`HighFidelityTrajectory.py`](../PyEMTG/HighFidelity/HighFidelityTrajectory.py) rewrites journeys and initial guesses.
- [`batch_convert_MGALT_to_PSFB_HiFi.py`](../PyEMTG/Converters/batch_convert_MGALT_to_PSFB_HiFi.py) runs the multi-stage conversion and parallelizes cases with `joblib` when available.
- [`JourneyOptions.ConvertDecisionVector`](../PyEMTG/JourneyOptions.py) can rename compatible MGALT/FBLT decision-vector entries.

The batch script is not yet a reliable product boundary: it builds shell command strings with output redirection, ignores structured return codes, mutates whole directories, contains a special-case match-point setting, and lacks a focused end-to-end test.

The extraction opportunity is to turn the research workflow into one deterministic continuation command/API:

1. validate the source mission and record its fidelity/transcription;
2. convert patched-conic flybys to integrated flybys;
3. map MGALT or MGALTS controls/states into FBLT;
4. optionally convert to PSFB;
5. run each continuation stage with explicit solver budgets;
6. stop with a structured diagnostic at the first failed stage;
7. emit a manifest containing source, options, metrics, and produced seeds.

What this buys is **convergence speed and analyst-time reduction**, not necessarily lower cost per integrated evaluation. It also creates the natural path for MGALTS solutions to seed FBLTS/PSFB later.

## P2: FBLTS

### Capability and fit

The 2019 paper says EMTG's high-fidelity Jacobians use integrated STMs and notes Sundman regularization with corresponding variational equations as a natural future direction. The local fork is unusually close to that future direction:

- `FBLTS` is reserved in enums/options and throws in the journey factory.
- The FBLTS header is a zero-byte placeholder.
- [`SundmanSpacecraftEOM.cpp`](../src/Astrodynamics/EquationsOfMotion/SundmanSpacecraftEOM.cpp) supports both uncontrolled and controlled dynamics and forms Sundman-transformed variational equations.
- The generalized-anomaly transform function exists, but the active code uses a scaled-radius transform; the generalized-anomaly call is commented out.
- [`FBLTphase.cpp`](../src/Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/FBLT/FBLTphase.cpp) already integrates augmented STMs and control columns.

This makes FBLTS a medium-high architectural fit but a large implementation. The current FBLT class owns concrete time-domain EOM and integration objects, so useful reuse will require extracting a propagation-domain/EOM strategy rather than merely subclassing and replacing one member.

### Expected payoff

For high-eccentricity finite-burn arcs, fixed time steps spend many evaluations where the state changes slowly and risk under-resolving periapsis. A Sundman domain can distribute work more geometrically. The expected benefit is the same state/Jacobian accuracy with fewer RHS evaluations and better conditioning of long eccentric arcs.

That is a hypothesis to test. The reviewed Ellison/Englander high-fidelity paper did not publish an FBLTS runtime result. FBLTS should proceed only after the anomaly/Sundman benchmark infrastructure exists and after MGALTS demonstrates value.

## P2: Q-Law partials and gain optimization

The [2021 Q-Law derivative paper](https://ntrs.nasa.gov/api/citations/20205008909/downloads/AAS%202021%20Partials%20Master.pdf) derives thrust-direction partials with respect to spacecraft state and five Q-Law gains, then integrates those sensitivities in an STM for an NLP. The [2023 journal version](https://doi.org/10.1007/s40295-023-00371-1) describes coupling a Q-Law planetocentric phase with a Sims-Flanagan interplanetary phase.

### Local seam

- [`ControlLawThrustPhase`](../src/Mission/Journey/Phase/TwoPointShootingPhase/CoastPhase/ControlLawThrustPhase/ControlLawThrustPhase.cpp) is a working integrated two-sided-shooting phase with STM-based match derivatives.
- [`ThrustTerm.cpp`](../src/Astrodynamics/AccelerationModel/ThrustTerm.cpp) already treats guidance direction and its state partials as part of the acceleration model.
- The current `ThrustControlLaw` enum contains only Cartesian input, velocity, and anti-velocity laws.
- EMTG also has analytic Edelbaum spiral boundary-event models, which provide a lower-fidelity comparison and possible seed source.

Q-Law therefore fits the propagation and derivative architecture, but the current control-law phase has no gain decision variables or gain-sensitivity STM columns. A native implementation needs more than another enum branch.

### Recommended sequence

1. Implement `QLawGuidance` as a pure math component returning thrust direction, `du/dstate`, and `du/dgains`.
2. Verify every branch and singular regime with complex-step/AD tests.
3. Add a standalone Q-Law trajectory/gain optimizer that emits an EMTG-compatible control/state history.
4. Compare it against the current Edelbaum spiral and direct FBLT/PSFB seeding.
5. Only then add a native `QLawThrustPhase` with gain decision variables and augmented STM columns.
6. Add hybrid coupling to MGALT/MGALTS through the same continuation framework described above.

What we get is a small-dimensional spiral optimization and strong initial guesses for planetocentric escape/capture. It does not speed ordinary heliocentric MGALT missions that contain no spiral.

Risks include singular classical elements, target-angle wrapping, eclipse/throttle discontinuities, coasting logic, and frame transitions. A modified-equinoctial internal representation may be preferable even if user-facing targets remain classical.

## P3: Sundman DDP and flyby-tree search

The [Sundman DDP paper](https://ntrs.nasa.gov/citations/20170001472) demonstrates fuel-optimal planetocentric transfers exceeding 1000 revolutions, including J2 and lunar gravity examples. This is a real capability gap, but DDP requires its own backward cost-to-go sweep, dynamics quadratization/second-order information, constraint handling, and control parameterization. EMTG's current inner loop is organized around sparse NLP interfaces, so embedding DDP as another `NLP_interface` backend would be misleading.

If extreme planetocentric spirals become a core requirement, build DDP as a separate library or PyEMTG service that produces a state/control seed for FBLTS/PSFB. Q-Law is the cheaper first seeder; DDP is the higher-capability specialist.

Ellison's dissertation abstract also describes a parallelized flyby-tree path finder for autonomous moon-tour design. No corresponding implementation was found in `src` or `PyEMTG`. This has high value only if discrete satellite-tour sequence discovery is an active product requirement. It should otherwise remain behind the continuous-transcription work above.

## What not to build from these papers

### Exact Hessians

No verified closed-form trajectory Hessian from the reviewed Ellison/Englander corpus maps into EMTG. [`IPOPT_interface.cpp`](../src/InnerLoop/IPOPT_interface.cpp) uses limited-memory Hessian approximation, which is consistent with the available first-order derivative architecture. Before considering second-order work, benchmark:

- MGALTS node/variable reduction;
- MBH screening;
- continuation quality;
- solver scaling and L-BFGS options;
- small-matrix allocation/copy cost in STM/MTM chains.

These are lower-risk routes to faster solutions.

### Rewriting existing analytic derivatives with general AD

AD is valuable as an oracle and for new equation-level tests. The 2018 paper found specialized analytic Jacobian evaluation faster than forward-mode AD and finite differences. Production-wide AD would trade away one of EMTG's existing advantages and should not replace the hand/STM derivatives that are already working.

## Proposed delivery order

1. Add the benchmark manifest and metrics collector.
2. Change MBH screening to avoid Jacobians for rejected candidates; tune the feature on fixed seeds.
3. Productize and test the MGALT -> high-fidelity continuation pipeline.
4. Implement and verify the anomaly-domain Kepler kernel.
5. Deliver a minimal MGALTS phase and one hard benchmark.
6. Reach MGALT feature parity and add conversion tooling.
7. Prototype Q-Law as a seeder.
8. Prototype FBLTS only after MGALTS/Sundman benchmarks justify it.
9. Revisit DDP and flyby-tree work only against concrete mission requirements.

## Primary public sources used

- Donald H. Ellison, Jacob A. Englander, and Bruce A. Conway, [“A Time-Regularized Multiple Gravity-Assist Low-Thrust Bounded-Impulse Model for Trajectory Optimization”](https://ntrs.nasa.gov/citations/20170001426), 2017.
- Donald H. Ellison et al., [“Analytic Gradient Computation for Bounded-Impulse Trajectory Models Using Two-Sided Shooting”](https://pmc.ncbi.nlm.nih.gov/articles/PMC7526664/), 2018.
- Donald H. Ellison et al., [“Application and Analysis of Bounded-Impulse Trajectory Models with Analytic Gradients”](https://experts.illinois.edu/en/publications/application-and-analysis-of-bounded-impulse-trajectory-models-wit), 2018.
- Donald H. Ellison and Jacob A. Englander, [“High-Fidelity Multiple-Flyby Trajectory Optimization Using Multiple-Shooting”](https://ntrs.nasa.gov/citations/20190028884), 2019.
- Donald Hamilton Ellison, [“Robust Preliminary Design for Multiple Gravity Assist Spacecraft Trajectories”](https://www.ideals.illinois.edu/items/107648), 2018 dissertation record and abstract.
- Jacob A. Englander and Arnold C. Englander, [“Tuning Monotonic Basin Hopping”](https://ntrs.nasa.gov/citations/20140007521), 2014.
- Jonathan D. Aziz et al., [“Low-Thrust Many-Revolution Trajectory Optimization via Differential Dynamic Programming and a Sundman Transformation”](https://ntrs.nasa.gov/citations/20170001472), 2017.
- Jackson L. Shannon, Donald Ellison, and Christine M. Hartzell, [“Analytical Partial Derivatives of the Q-Law Guidance Algorithm”](https://ntrs.nasa.gov/api/citations/20205008909/downloads/AAS%202021%20Partials%20Master.pdf), 2021.
- Jackson L. Shannon, Donald H. Ellison, and Christine M. Hartzell, [“Analytic Calculation and Application of the Q-Law Guidance Algorithm Partial Derivatives”](https://doi.org/10.1007/s40295-023-00371-1), 2023.

## Claim boundaries

- Published mission examples establish capability and solution-quality behavior; they are not automatically runtime guarantees on this fork.
- “Expected” benefits above are engineering hypotheses that require the proposed benchmarks.
- The local code mapping is to the stated commit and includes this fork's newer IPOPT and adaptive-propagator work, not only NASA's public master branch.
- Existing user changes and untracked dependencies/ephemeris files were not modified or included in this analysis.
