# Outer-loop developer architecture

## Component boundaries

`PyEMTG/OuterLoop` is split into pure search code and side-effecting execution code. `model`, `canonical`, `randomness`, `genome`, `operators`, `rules`, `physics`, `resonance`, `objectives`, and `nsga2` can run without EMTG. `evaluator` owns current `.emtgopt` adaptation and result parsing; `process` owns subprocess isolation. `storage`, `archive`, `seeds`, `fidelity`, `workers`, and `campaign` supply durability and orchestration. `cli`, `reporting`, `analytics`, `legacy`, and `qualification` are user-facing layers.

The evaluator protocol is `context_identity()` plus `evaluate(EvaluationRequest) -> EvaluationResult`. Synthetic and external implementations therefore exercise the identical search, cache, checkpoint, and archive path.

## Identity and deterministic execution

- `individual_id` identifies a lineage slot and is derived from root seed, trial, generation, and slot.
- `phenotype_id` hashes the repaired decoded architecture. Repair provenance and inactive payload are excluded.
- `evaluation_key` hashes phenotype, fidelity, concrete inner seeds, budget, initial guess, and evaluator/campaign context.
- `comparison_context_id` hashes common executable/assets, fidelity, solver/seed/provider policy, extraction, and scoring context. Archives are keyed by comparison context, trial, and fidelity.

Canonical JSON uses sorted UTF-8 keys, explicit decimal encodings, and rejects non-finite numbers. An inner seed derives from trial, phenotype identity, fidelity, and repeat index, so equivalent phenotypes deduplicate while different stochastic trials cannot collide.

Selection sees results only after they are restored to population-slot order. Workers may persist completions immediately. Restart reconstructs requests from stable candidates and stateless seed coordinates, not Python's global RNG state.

## EMTG adaptation

Logical destination names map to one-based `destination_list` universe indices. Flybys map independently through the one-based positive-altitude flyby menu. The adapter clones a base mission, deep-copies an explicit journey template, applies registered genes, clears incompatible trial vectors, and writes all options into an isolated directory.

Direct NLP uses `run_inner_loop=3`; MBH uses `1`; propagation-only TrialX uses `0`. IPOPT is `NLP_solver_type=2`. Seeds are disassembled into journey trial vectors only after structural compatibility succeeds.

Output classification checks nominal versus `FAILURE_` files, objective/constraint fields, feasible-attempt fields, vector consistency, and required metrics. EMTG currently catches some failures and exits zero, so the process return code is secondary evidence.

The safe universe parser does not reuse legacy `Universe`/`Body` classes because those evaluate input strings. The current generated MissionOptions `user_data` parser and generator use `ast.literal_eval` with a top-level tuple splitter. Existing top-level imports remain supported.

## Persistence and faults

SQLite runs in WAL/FULL-synchronous mode. Generation candidates are written before submission, and results are committed as each worker completes. `checkpoint.json` is a replace-atomic mirror of database state. A crash can lose an active process but not a committed evaluation; resume only submits rows still lacking results.

Cache records and solver artifacts use SHA-256 paths and same-key cross-process locks. A conflicting same-key result is rejected instead of overwritten. Execution attempts remain separate while the first canonical raw result is immutable. Pending/running/cancelled records are never cached as completed results.

Windows workers use Job Objects with kill-on-close; POSIX workers use new sessions/process groups. Thread-count environment variables default to one to prevent nested oversubscription.

## Selection, seeds, and archives

NSGA-II implements fast nondominated sorting, normalized crowding, deterministic tournaments, and parent-plus-offspring survival. Feasible trajectories dominate completed infeasible ones; normalized violation orders defensible infeasible results, followed by typed failure severity. Missing objective dimensions make a result incomplete rather than assigning a numerical fiction.

Archives are exact and comparison-context/trial/fidelity-specific. Equal objective vectors may retain distinct canonical architectures. Aggregate reporting preserves scope labels and never performs dominance across them.

Seed artifacts carry ordered descriptions, vectors, structural fingerprints, fidelity, quality, and family. Same-generation solutions are deliberately excluded from seed selection: this makes interruption/resume identical to a batch whose requests were fixed before worker completion. Prior generations and lower fidelities may seed later work when policy permits.

## Extension contracts and deliberate limits

- `WorkerBackend`: local implementation is complete; Slurm, MPI, PEATSA, and managed queues implement the same request/result contract and are not launched implicitly.
- `ExternalSeedProvider`: strict schema-3 JSON file protocol for R-FFS and other generators. R-FFS itself remains external.
- Built-in conversion adapters wrap the existing multi-to-single-phase and MGALT/FBLT-to-PSFB utilities only when their structural and artifact preconditions pass.
- `PhysicsScreenProvider`: conservative two-body screens are included; a production Lambert implementation may be supplied without changing evolution.
- Resonance mutation is an independently designed two-body equivalent using universe moon data and configurable integer ratios. It is not claimed to reproduce undisclosed institutional code.
- NSGA-III and GUI work are outside the baseline. The interfaces do not label NSGA-II as a strong many-objective algorithm.

## Legacy mapping

| Legacy artifact/option | Modern implementation |
|---|---|
| V1 population/generation/tournament/crossover/mutation/stall fields | typed `NSGA2Config` |
| null choice in body menu | activation tag plus retained hidden payload |
| maximum flybys/journeys and stop-after | hierarchical slots and canonical truncation |
| warm population/archive | versioned JSONL or explicit legacy gene mapping |
| archive/population files | SQLite primary, JSONL/CSV and safe `.NSGAII` compatibility |
| `bulletproof.py` timestamp scanning | transactional checkpoint with explicit schema/status |
| PEATSA fingerprint and seed criteria | typed sequence-aware seed inventory, no `eval()` |
| PEATSA local oven | safe evaluator plus deterministic worker backend |
| positional point-group lists | named declarative groups and typed score/constraint metadata |
