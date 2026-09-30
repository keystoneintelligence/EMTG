# Native scientific results

`PyEMTG.Results` requires only Python's standard library. Import
`EMTGResultParser` and call `parse(path, failure_file=False)` to obtain a
`ParsedEMTGResult`. The native output and parser behavior are preserved;
`complete`, `feasible`, and `failure_reason` remain distinct.
The historical `OuterLoop.evaluator` parser import remains a re-export.

`artifact_inventory({"emtg-output": output_path, "options": options_path})`
returns the versioned `emtg-artifacts/v1` inventory: artifact roles, names,
sizes, SHA-256 hashes, explicit missing entries, native event-table units,
and per-journey frame/central-body declarations. Unknown context is null or
empty. Supply a time system only when known from the run's inputs; the
inventory does not infer a central body, target, objective, or convert frames.
Native files are never rewritten by inventory or result parsing.

OuterLoop continues to own optional search, continuation, checkpoints, and
computational caches. Evaluations return their native artifacts. Callers own
publication, application metadata, and translation into service formats, and
perform these operations after evaluation. No service is required by EMTG.
