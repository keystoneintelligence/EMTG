# Outer-loop configuration and API reference

## Version and top-level fields

JSON configurations use `schema_version: "outerloop/v2"`. Internal persisted state is schema 3 and intentionally has no migration path from pre-production schema-1/2 runs.

The validated top-level fields are `base_case`, `run_directory`, `root_seed`, `assets`, `search`, `objectives`, `algorithm`, `evaluator`, `operators`, `constraints`, `groups`, `prefilters`, `fidelities`, `seeds`, `workers`, `checkpoint_every`, `resonance`, `templates`, `resources`, `cache`, `checkpoints`, and `outputs`. Unknown fields are errors at every validated level. Executable, universe, hardware, CSPICE `brief`, and solver-capability paths belong in `assets`; paths resolve relative to the configuration file.

`search` defines maximum/minimum journey and flyby counts, fixed endpoints, chaining, `tags`, `count`, or `tags_and_count` activation, ordered named repairs, the flyby menu, and mission/journey/phase gene maps. Genes are `fixed` with `value`, `choice`, bounded `integer`, or bounded quantized `decimal`. Booleans use a two-value choice. Supported repairs are `compact`, `reconnect_endpoints`, `group_replace`, and `clamp_bounds`; the empty list means rejection. Fidelity is an evaluation dimension, not a gene.

`objectives` contains names or objects with `name`, `direction`, `scale`, `units`, `source`, `missing_policy`, `penalty`, and `valid_for_infeasible`. Missing values default to `reject`; `penalize` requires an explicit finite penalty. Per-candidate omission is not supported because NSGA-II requires fixed-dimensional vectors. Outer-constraint infeasibility is distinct from EMTG solver infeasibility.

`evaluator.type` is `synthetic` or `emtg`. EMTG additionally validates its executable, timeout, base case, universe/hardware paths, `solver_capabilities.json`, inner-loop mode, NLP solver, transcription choices, template/phase-expansion policy, ephemeris override, and environment. `templates.journeys` maps logical names to base journey indices. Trial vectors are never inherited. Constraints are cleared after topology changes unless their text matches an explicit `constraint_migration_allowlist` token.

`prefilters` always require `type`. Strict topology filters and audited heuristics are distinct. Audit overrides and observed false rejections are persisted. Lambert and patched-conic screens use a strict schema-3 JSON subprocess provider and are never treated as no-op filters.

`fidelities` require unique names and contiguous ranks beginning at zero. Each has a strict solver budget and optional promotion count/fraction. `workers` is an object with local `count`, `infrastructure_retries`, and `backend: "local"`. `resources` accepts positive POSIX CPU/address-space/process limits and Windows Job Object memory/process limits.

## Gene and objective support

| Scope | Supported production adaptations |
|---|---|
| Mission | launch epoch/window, scalar or bounded total flight time, launch vehicle, spacecraft/power/propulsion selections, EP string count, duty cycle, propellant capacities, departure/final-arrival envelopes |
| Journey | endpoints, named template, central body, boundary types, transcription, DSM count, journey/wait bounds, periapse-burn enablement |
| Phase | transcription and DSM/impulse count through validated one-phase journey expansion |
| Evaluation only | fidelity, inner seed set, budget, timeout, resource limits |

Verified parser metrics include flight time, final/dry/delivered mass, dry-mass margin, propellant components, deterministic delta-v, launch/arrival geometry, beginning-of-life and bus power, duty cycle, thruster count, normalized aggregate control, the complete mission-event table, decision/constraint vectors, and relevant supplemental archive/summary artifacts. Architectural counts, point-group scores, convergence probability, and runtime are derived with their source recorded in objective metadata.

## Cache identity

Raw results alone are cached. Solver artifacts are copied into an immutable SHA-256 store. Same-key writes with different evaluation content are rejected. `evaluation_key` includes concrete phenotype, seeds, budget, and initial guess; `comparison_context_id` covers the common executable/assets, solver/seed/provider policy, fidelity, scoring, and extraction context. Pareto archives are partitioned by comparison context, trial, and fidelity and are never dominance-merged across scopes.

Point-group scores and derived resonance opportunities do not affect phenotype identity. Actual mission topology and deliberately selected resonance ratios do.

## Seed compatibility

| Path | Required compatibility |
|---|---|
| Exact transfer | identical ordered target `Xdescriptions` |
| Same-shape body substitution | exact descriptions plus journey/phase counts, transcription, DSM counts, boundary classes/types, constraints, and hardware |
| Multi- to single-phase | validated wrapper around `convert_to_single_phase_journeys`, with target endpoint/vector checks |
| MGALT/FBLT to PSFB | validated wrapper around the existing TPSLT-to-PSFB converter; single-phase/source-artifact preconditions are mandatory |
| Other length/transcription change | target-native external provider response or explicit rejection |
| Low-to-high propagation | same checks, with fidelity transfer explicitly enabled |

Schema-2 inventory files and modern JSONL exports can be discovered from configured folders. Providers use a version-2 request/result protocol, executable/content identity, timeout, cancellation, process-tree cleanup, and logs. Rejection reasons are stored for every selected seed considered for conversion.

## Public Python API

`PyEMTG.OuterLoop` exports `Campaign`, `CampaignConfig`, `Evaluator`, `SeedProvider`, `SeedConverter`, `SeedConverterRegistry`, exact/same-shape/single-phase/TPSLT-to-PSFB converters, `WorkerBackend`, `QueueTransport`, serializable queue records, `FakeQueueBackend`, objective/constraint/operator registries, `EvaluationResult`, `ScoredEvaluationResult`, `ArtifactRef`, `ComparisonContext`, genotype/phenotype records, `GenomeSchema`, and NSGA-II types.

The distributed queue protocol is implemented and contract-testable. Concrete Slurm, MPI, PEATSA, and cluster launchers are extension packages rather than core claims.
