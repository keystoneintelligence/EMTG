# EMTG autonomous outer loop

The Python outer loop searches discrete mission architectures while EMTG's existing MBH and NLP machinery continues to optimize each architecture's continuous trajectory. It supports fixed and variable journey/flyby counts, hidden/null genes, multiple objectives, parallel isolated EMTG runs, durable caching, exact restart, neighboring-case seeds, and fidelity promotion.

## Quick start

From the repository root:

```powershell
python -m PyEMTG.OuterLoop validate PyEMTG/OuterLoop/examples/synthetic-sequence.json
python -m PyEMTG.OuterLoop run PyEMTG/OuterLoop/examples/synthetic-sequence.json
python -m PyEMTG.OuterLoop status _local/outerloop-synthetic-v2
python -m PyEMTG.OuterLoop export _local/outerloop-synthetic-v2 --plot flight_time delivered_mass
python -m PyEMTG.OuterLoop qualify PyEMTG/OuterLoop/examples/synthetic-sequence.json --maximum-architectures 100000
```

`run` resumes its own run directory when a checkpoint exists. `resume CHECKPOINT` instead uses the immutable `resolved-config.json` stored beside the checkpoint. Use `inspect`, `rerun`, and `promote` to audit, repeat, or write a feasible result as a standalone `.emtgopt`. Promotion embeds the parsed optimized decision vector, selects propagation-only `run_inner_loop=0`, and directs output to the export directory; use `rerun` when re-optimization is desired.

## Variable ownership

| Owner | Appropriate variables |
|---|---|
| Outer loop | bodies, sequence length, journey count, transcription choice, DSM count, boundary architecture, hardware keys, launch-window choices, time bounds |
| EMTG inner loop | event epochs within bounds, phase times within bounds, states, controls, masses, impulses, flyby geometry, NLP variables |
| Explicit shared boundary | outer-selected windows/bounds and a verified `trialX`; the outer loop does not independently optimize the same continuous value |

A gene is declaratively `fixed`, `choice`, `integer`, `decimal`, or `boolean`. Decimal genes require a resolution, making serialization and evaluation identities platform-stable. Omitted options remain owned by the base `.emtgopt`.

## Variable-length missions

The chromosome has a configured maximum number of journey and flyby slots. Each slot contains an activation tag and a hidden payload. Inactive payload remains available to later crossover/mutation but has no effect on topology, case generation, or the canonical phenotype hash. Active slots compact in slot order; journey endpoints and phases are rebuilt without empty placeholders.

`activation_mode` selects tag-only, count-only, or tag-and-count behavior. `repair_policy` defaults to `reject`. `compact`, `group_replace`, and `compact_group_replace` are explicit, logged alternatives. Group repair only replaces an existing flyby; it refuses to invent a new phase silently.

Current EMTG stores `phase_type` and `impulses_per_phase` at journey scope. If phase payloads differ, the case adapter expands that decoded journey into connected one-phase journeys with flyby/intercept boundaries. It rejects this architecture when `expand_phase_genes` is disabled. Fidelity is never a phase gene. Periapse-burn selection is supported only as a journey gene.

## Results and failures

The outer loop never infers success from EMTG's return code. Status values distinguish structural invalidity, strict and heuristic filtering, process failure, timeout, incomplete output, completed EMTG infeasibility, outer-constraint infeasibility, and feasibility. Missing objectives either reject the result or use an explicitly configured finite penalty.

Each run contains:

- `campaign.sqlite`: separate raw evaluations, scored campaign associations, populations, fidelity archives, promotions, and provenance;
- `checkpoint.json`: atomic human-readable restart state;
- `cache/`: immutable context-addressed results;
- `cases/`: isolated options, logs, and EMTG artifacts;
- `campaign-summary.json`: duplicates, status counts, runtime, seed rates, diversity, and archive indicators;
- `exports/`: JSONL, CSV, legacy `.NSGAII`, convergence history, and optional plots.

Heuristic filters are disabled unless configured. Each may specify `audit_fraction`; audited rejects are still evaluated so false-rejection risk can be measured. Strict topology, asset, solver, hardware, body-menu, and ephemeris checks cannot be audited away.

## Seeds and fidelity

Seed selection considers family, quality, endpoints, flyby subsequences, journey/phase counts, transcription, DSM counts, boundary types, constraint structure, epoch/time, and hardware. `exact_descriptions` requires identical ordered target `Xdescriptions`. `same_shape_body_substitution` additionally requires identical journey/phase counts, transcription, DSM counts, boundary classes/types, constraint structure, hardware, and ordered descriptions. Every considered seed receives a persisted accept/reject reason. Length or transcription changes require a registered converter or a target-native provider response.

An external seed provider receives a versioned JSON request and writes a JSON response. This is the supported boundary for R-FFS, Lambert, patched-conic, known-mission, and user generators. Provider responses are cached by request content.

Fidelity levels are ranked and compared separately. Evolution uses rank zero. Nondominated, crowded architectures are promoted to the next level and the final level is recorded as the confirmation archive. Low/high promotion outcomes are retained even when confirmation fails. `export` defaults to `confirmed` (or the highest-ranked name) for a ladder; use `--fidelity NAME` or `--fidelity all` explicitly.

## Reproducibility and safety

Random streams derive from the root seed and immutable trial/generation/operator/slot coordinates. Equivalent phenotypes in one evaluation context share an inner seed and cache entry. Worker completion order affects only when results are persisted, never parent selection. EMTG receives an argument vector with `shell=False`, per-case working directories, bounded runtime, and process-tree cleanup.

Internal campaign, checkpoint, cache, phenotype, result-extraction, queue, and provider protocols are schema 3. User configuration is `outerloop/v2`. Pre-production schema-1/2 databases are never migrated or deleted; select a fresh run and cache directory. See [the configuration and API reference](outerloop_configuration.md) for the identity fields, support matrices, and typed public API.

NSGA-II is the baseline. Four or more objectives trigger a many-objective warning because crowding distance generally loses discrimination; the tool does not claim NSGA-III behavior.
