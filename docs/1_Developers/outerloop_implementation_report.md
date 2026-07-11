# Autonomous outer-loop implementation report

This living report is updated from executed commands rather than assumed results.

## Schema-3 gap-closure implementation

The production contract is now `outerloop/v2` with fresh internal schema 3. Pre-production state is intentionally not migrated. Configuration now has typed assets and workers, strict solver budgets, explicit fixed/choice/integer/decimal genes, ordered named repairs, finite objective penalties, mandatory/optional capped point groups, and distinct outer-constraint infeasibility.

Evaluation artifacts are copied into an immutable SHA-256 store; conflicting same-key cache/database writes are rejected. Raw solver results remain separate from campaign scoring. Exact archives and exports are partitioned by comparison context, trial, and fidelity. Physics provider filters execute a strict subprocess protocol, heuristic audits record false rejections, worker queue records have a serializable schema-3 wire form, and promotion checks recorded source/artifact hashes unless a non-reproducible override is explicit.

Stratified initialization, bounded deterministic operator retries, and proposed/effective/no-op/rejected/accepted statistics address the measured duplicate/no-op problem. Real fixture tests exercise both approved seed adapters: multi-phase to single-phase journeys and MGALT/FBLT to PSFB. The ten-trial production, fixed Earth-Mars, and Jupiter resonance manifests are present; their generated reports remain the authority for completion status.

## Evidence reviewed

The public-source review is recorded in [outerloop_public_bibliography.md](outerloop_public_bibliography.md). It covers the 2012 nested evolutionary/continuous architecture, the 2013 null-gene dissertation record, 2015 NSGA-II and memoization work, 2016 variable-sequence MGAnDSMs work, later null/HGGA publications, PEATSA neighbor seeding, the public resonance-mutation record, and R-FFS initial-guess integration. Where only a capability or abstract is public, the corresponding implementation is explicitly an independent equivalent.

Repository forensics covered the V1 mission/journey option models; historical NSGA-II population, GUI, filter, and `bulletproof.py`; PEATSA execution, menu, seeding, and fingerprint code; current generated option APIs and generators; decision-vector conversion utilities; `problem.h` and option C++ sources; mission factories and phase implementations; user/developer documentation; Testatron; mission examples; and branch/file history. Legacy modules remain evidence or compatibility adapters rather than the production foundation.

## Capability matrix

| Capability | Status |
|---|---|
| Fixed/variable flyby and journey counts | Implemented with hidden activation slots and canonical decoding |
| Inactive-gene neutrality and phenotype cache identity | Implemented and tested |
| NSGA-II, single/multiobjective, constraints, deterministic ties | Implemented; ZDT1 recovery and primitive tests included |
| Point groups, filters, explicit repair | Implemented |
| Current EMTG case generation and safe result parsing | Implemented and exercised in a bounded exhaustive real campaign |
| Local parallel isolation, timeout, cancellation, immediate persistence | Implemented |
| Durable cache/checkpoint/resume | Schema-3 raw/scored storage, immutable content artifacts, context/trial/fidelity archives, SQLite/WAL, and atomic files |
| Neighbor seeds, multiple seed attempts, external providers | Implemented; exact/same-shape transfer plus validated single-phase and MGALT/FBLT-to-PSFB adapters |
| Multi-fidelity promotion and confirmation | Implemented with separate archives |
| Resonance-aware moon-tour extension | Radius-crossing and required/available-turn screening implemented; focused Jupiter-system gate recovered the exact front in 10/10 trials |
| R-FFS | External JSON provider boundary implemented; R-FFS algorithm intentionally not reconstructed |
| PEATSA/Slurm/MPI concrete scheduler | Extension contract only; no external jobs are launched |
| NSGA-III/GUI | Deferred and not claimed |

## Initial forensic baseline

- Branch base: `e3013126715043870d221f9ed8eca9b4d2ffb101`.
- Python baseline before implementation: 48 tests passed.
- C++ baseline: `ci-fast` 4/4 and `ipopt-only` 5/5 passed.
- A bounded Testatron smoke invocation loaded the local kernels and executable but produced an infeasible `FAILURE_*.emtg` while the process returned zero. This directly motivated artifact-based result classification.
- Locally usable assets: `bin/EMTGv9.exe`, IPOPT runtime and capability manifest, Testatron universe/hardware files, CSPICE `brief`, and the pre-existing SPICE kernels.

## Scientific limitations and risk

The resonance paper's public abstract does not disclose enough details for exact operator reproduction. The implementation uses public two-body resonance/turning physics and records that provenance. Hohmann/C3 screens are optional heuristics with audit sampling, not truth models. No built-in R-FFS or high-quality Lambert solver is claimed. Seed transfer across changed transcription or vector descriptions is rejected unless an explicit provider/converter supplies a target-native vector.

NSGA-II crowding is not a strong many-objective method. Real front recovery depends on inner-loop convergence probability and the configured budget; low-fidelity and confirmed results remain separate. Concrete cluster backends and an overnight moon-tour campaign require external execution authority and are not part of fast repository tests.

## Implemented genes, metrics, and policies

Mission genes are registry-applied for journey count/activation, stop-after, epoch/window and explicit scalar/bounded time choices, launch vehicle, spacecraft/power/propulsion files and enumerated hardware, EP string count, duty cycle, propellant capacities, and C3/arrival-envelope choices. Journey genes cover endpoints, named templates, central body, boundary types, transcription, timing bounds, and periapse-burn selection. Phase genes cover transcription and DSM/impulse count through journey expansion. Fidelity is not a gene. A configuration fixes or omits any of these; omitted continuous controls remain EMTG-owned.

The objective registry includes flight time, launch epoch, delivered and returned mass, departure/arrival C3, arrival declination, entry velocity, deterministic delta-v, EMTG objective, propellant, journey/flyby counts, mass/power/duty/thruster metrics, hardware preferences, aggregate control, point-group score, convergence probability, and runtime. Custom current EMTG metrics may be registered declaratively. Selected raw values are stored by name while NSGA-II receives direction- and scale-normalized vectors. Structural invalidity, strict/heuristic filtering, process failure, timeout, incomplete output, completed infeasibility, and feasibility remain distinct.

Repair defaults to rejection. Compaction and group replacement are opt-in and recorded. Heuristic filters are opt-in and auditable. Strict checks cover topology, universe/menu mappings, endpoint and phase compatibility, time ordering, solver capabilities, hardware presence, and configured ephemeris coverage. Optional two-body time/C3/propulsion and resonance screens are advisory physics models, not substitutes for EMTG.

## Qualification results

Executed commands and outcomes:

```text
python -m pytest -p no:cacheprovider
113 passed, 1 skipped in 31.00s

cmake --build --preset ci-fast
ninja: no work to do

ctest --preset ci-fast --output-on-failure
4/4 passed

ctest --preset ipopt-only --output-on-failure
5/5 passed, including EMTG.ipopt_interface_tests

EMTG_RUN_OUTERLOOP_INTEGRATION=1 python -m pytest tests/test_outerloop_emtg_integration.py -p no:cacheprovider
1 passed in 6.91s
```

Fresh schema-3 qualification executed on 2026-07-10/11 produced the following release evidence:

- The fixed Earth-Mars real-EMTG local gate enumerated one architecture, found it feasible, recovered the one-member front exactly, and passed with recall 1.0.
- The variable zero-to-two-flyby Earth-Mars local gate enumerated seven distinct architectures. All seven were feasible and the evolutionary archive recovered both exact Pareto members, so the local gate passed with recall 1.0. Five truth contexts were cache hits and two required EMTG execution; aggregate recorded inner runtime was 102.812 seconds.
- The general synthetic production problem enumerated 18,060 hidden genotypes and 6,020 canonical phenotypes with 110 true Pareto architectures. Ten independent trials produced median recall 0.65 and zero exact recoveries (95% Wilson interval approximately 0.0 to 0.278). The gate correctly failed rather than weakening the acceptance threshold.
- The focused Jupiter resonance problem retained variable one-to-four-moon sequences, phase type, and hardware while fixing DSM count to keep exact truth bounded. It enumerated 1,360 unique feasible architectures with a two-member Pareto front. All ten trials recovered that front exactly; median recall was 1.0, exact recovery was 10/10, and the production gate passed.

Authoritative generated reports are under the ignored `_local/outerloop-earth-mars-fixed-v3-release`, `_local/outerloop-earth-mars-qualification-v3`, `_local/outerloop-synthetic-production-v3-release`, and `_local/outerloop-jupiter-resonance-v3-release` directories.

The prior synthetic finite-space study enumerated 18,060 genotypes and 6,020 canonical phenotypes. Its small search recovered 16 of 70 reported front members (22.86%). Those historical artifacts predate internal schema 2 and are evidence only; they cannot resume or populate current production qualification state.

The prior bounded Earth-Mars study used different stochastic contexts for evolutionary and exhaustive runs. Its reported 100% front match is therefore a smoke result, not valid stochastic Pareto truth under the schema-2 qualification rule. Current qualification must configure one named repeated-inner seed set, which derives seeds from phenotype identity and is shared by exhaustive and evolutionary searches.

A fresh schema-2 bounded smoke subsequently evaluated all seven zero/one/two-flyby Earth-Mars architectures with one named three-seed set shared by evolution and exhaustive truth. Five exhaustive contexts were cache hits, two were evaluated, all seven aggregate results were feasible, and the two-member exact front was recovered 2/2. A representative standalone case reproduced as feasible with `J = -0.173292`; completed-state resume used zero new evaluations. This closes the bounded context-equivalence smoke only; it is not the full local or overnight production gate.

The schema-2 synthetic variable-journey/mixed-transcription/hardware study completed two trials and recovered 21 of 70 exhaustive front members (30% recall). Its exhaustive pass covered 18,060 genotypes/6,020 phenotypes and completed after a command-boundary interruption by reusing 3,930 persisted cache entries. The result is intentionally reported as below target.

## Guarantees and remaining risks

SQLite/WAL commits candidates before dispatch and each result on completion. Checkpoints use an explicit schema and atomic replacement. Stable coordinate-derived random streams make interrupted/resumed synthetic campaigns identical to uninterrupted campaigns and invariant to worker count/completion order in tests. Cache identity covers the repaired phenotype, fidelity, stochastic seed, budget, initial guess, base options, executable/runtime, commit, solver settings, and mission assets. Fidelity archives are separate and final promotion is explicit.

Converter and provider protocol unit behavior is implemented, but matched real seeded/unseeded benefit is still unverified. No matched worker-count timing study was run; parallel correctness is tested but scaling is not claimed. The local fixed and variable Earth-Mars gates and the focused ten-trial Jupiter resonance gate passed. The broader synthetic production gate did not meet its scientific convergence target (median recall 65%, zero exact recoveries), so production completion remains incomplete. A top-fidelity real multi-fidelity confirmation campaign also remains unexecuted. R-FFS algorithms, concrete PEATSA/Slurm/MPI launchers, NSGA-III, and GUI remain extension/deferred items. No discovered architecture is labeled novel.

Current EMTG mission behavior remains backward compatible: no C++ mission semantics changed. Package-safe imports and literal numeric/user-data parsing modernize Python readers while preserving top-level legacy imports. Pre-existing modified runtime DLLs, IPOPT sources, kernels, and `docs/research_to_runtime_roadmap.md` were not overwritten or adopted as outer-loop changes.
