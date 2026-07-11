# Outer-loop qualification procedure

## Test tiers

The selected deep envelope permits up to ten minutes for PR tests, two hours for local qualification, and overnight manual campaigns.

Fast gates:

```powershell
python -m pytest -q -p no:cacheprovider
cmake --build --preset ci-fast
ctest --preset ci-fast --output-on-failure
ctest --preset ipopt-only --output-on-failure
```

Synthetic exhaustive truth and GA recovery:

```powershell
python -m PyEMTG.OuterLoop run PyEMTG/OuterLoop/examples/synthetic-sequence.json
python -m PyEMTG.OuterLoop qualify PyEMTG/OuterLoop/examples/synthetic-sequence.json --maximum-architectures 100000
python -m PyEMTG.OuterLoop export _local/outerloop-synthetic
```

Schema-3 ten-trial production assessment:

```powershell
python -m PyEMTG.OuterLoop qualify PyEMTG/OuterLoop/examples/synthetic-production.json --suite nightly --maximum-architectures 100000
python -m PyEMTG.OuterLoop qualify PyEMTG/OuterLoop/examples/jupiter-resonance-nightly.json --suite nightly --maximum-architectures 2000
```

Bounded local EMTG campaign:

```powershell
python -m PyEMTG.OuterLoop validate PyEMTG/OuterLoop/examples/emtg-earth-mars-qualification.json
python -m PyEMTG.OuterLoop run PyEMTG/OuterLoop/examples/emtg-earth-mars-qualification.json
python -m PyEMTG.OuterLoop qualify PyEMTG/OuterLoop/examples/emtg-earth-mars-qualification.json --maximum-architectures 100
```

The fixed comparison uses `emtg-earth-mars-fixed-qualification.json`. The Jupiter-system resonance campaign uses `jupiter-resonance-nightly.json`. `production-gate.json` contains per-trial reports, median recall, exact-recovery count, and the Wilson interval.

The Jupiter fixture deliberately fixes DSM count while varying one-to-four moon encounters, phase type, and hardware. This keeps exhaustive truth at 1,360 architectures. Expanding DSM count from zero through two increases the hidden finite space to 271,440 genotypes and is outside the bounded exact-qualification envelope; it belongs in a non-exhaustive manual campaign.

The three-architecture qualification example uses locally present IPOPT, Earth/Mars/Venus universe assets, SPK coverage checks, and isolated five-second MBH budgets. `emtg-earth-mars.json` is the larger multi-fidelity example. Generated runs belong under `_local` and are not committed.

## Acceptance measurements

`campaign-summary.json` records proposed and unique phenotypes, duplicate/dedup counts, typed status counts, feasible rate, runtime, seed-associated rates, archive size, exact two-objective hypervolume with its reported dynamic reference, and canonical architecture diversity. `qualification/exhaustive-*.json` records the enumerable architecture count, phenotype deduplication, cache state, filtered/evaluated/feasible counts, true Pareto IDs, recovered IDs, recall, and runtime.

For the overnight suite, run ten explicit root seeds or `algorithm.trials=10`. Target median Pareto recall is at least 90%, with complete recovery in at least eight trials. Report binomial intervals rather than relabeling a different hidden genotype as a novel architecture. Parallel scaling and seed benefit require matched campaigns differing only in worker count or seed policy.

Real EMTG feasibility is scientifically asset-, solver-, seed-, and budget-dependent. A timeout or infeasible output demonstrates correct infrastructure classification, not successful trajectory recovery. Final qualification must retain exact options, binary/asset hashes, seeds, and pass/fail counts.

Stochastic truth and evolutionary comparisons must configure the same `seeds.qualification_seed_set`, for example `{"name": "production-v1", "seeds": [0, 1, 2, 3, 4]}`, with `evaluator.inner_trials` equal to the number of entries. Seeds then derive from the phenotype and named set, never the outer-loop trial. Fronts produced under different MBH seed sets are not comparable qualification evidence.

## Pre-schema-2 evidence from 2026-07-10

The synthetic evolutionary run used 832 lineage proposals. Canonicalization reduced these to 178 unique phenotypes and 225 distinct evaluation contexts; 607 associations were deduplicated or cache-backed. It recorded 106 feasible, 102 heuristic-filtered, and 17 structurally invalid results and retained 24 nondominated architectures. The exact enumerator reduced 18,060 explicit hidden genotypes to 6,020 unique phenotypes, rejected or filtered 4,843, evaluated 1,177, and found an exact 70-member Pareto set. The intentionally small search recovered 16 members (22.86%). This is evidence of correct exhaustive comparison and a measured insufficient search budget, not evidence of complete convergence. The documented overnight target remains unverified.

The real Earth-to-Mars sequence smoke search proposed 40 lineage slots and evaluated three unique architectures. Its evolutionary and exhaustive runs used different stochastic contexts, so the previously reported front match is not schema-2 qualification evidence. All associated databases, checkpoints, phenotype IDs, and cache entries are evidence only and must not resume.

No matched worker-count or seeded/unseeded real campaigns were run, so parallel speedup and causal seed benefit remain unverified. The required local and overnight schema-2 gates remain pending; production completion must not be declared until they pass. No architecture is claimed novel.

## Schema-2 bounded smoke executed 2026-07-10

The fresh schema-2 Earth-Mars campaign enumerated seven canonical architectures spanning zero, one, and two flybys from the Earth/Venus menu. Every architecture used the same named three-seed inner set in evolutionary and exhaustive evaluation. Evolution evaluated five unique contexts; exhaustive evaluation hit those five cache entries and evaluated the remaining two. All seven aggregate results were feasible. The exact archive contained two architectures and evolutionary recall was 100% (2/2) for this bounded menu. Aggregate recorded inner runtime was 103.812 seconds.

One representative archive member was exported with its optimized decision vector and run standalone in propagation mode. EMTG reported `J = -0.173292`, feasibility `6.30976e-06`, and a feasible decision vector. A completed campaign resumed with zero new evaluations. Generated binaries, kernels, databases, caches, and cases remain under ignored `_local` paths.

This smoke does not satisfy the remaining local matrix (separate matched fixed/variable EMTG searches, real mixed phase/hardware finite spaces, matched worker counts, and seeded/unseeded benefit) or any overnight ten-trial/resonance gate. Production status remains incomplete.

The schema-2 synthetic variable-journey/mixed-transcription/hardware campaign also completed two trials, stalling at generation 12 after 117 unique evaluations and retaining 29 archive members. Exhaustive enumeration reduced 18,060 genotypes to 6,020 phenotypes, with 4,856 structural/filtered outcomes, 1,164 feasible evaluations, and a 70-member exact front. Evolution recovered 21 members (30% recall), well below the production target. The exhaustive command was interrupted once at the five-minute command boundary and completed from persisted cache evidence on the next invocation (3,930 pre-run hits). This validates persistence and comparison mechanics, not search adequacy.
