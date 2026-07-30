# EMTG solution packages

EMTG's storage-neutral downstream format is a DeepSpace package (`.dspkg`).
The archive contains a canonical manifest, the native `.emtg` result, a
normalized solution summary, event trajectory JSON, checksums, and any
additional run artifacts supplied by the caller.

Outer-loop EMTG evaluations produce this package automatically as the
`solution-package` artifact. A standalone EMTG result can be exported with:

```powershell
python -m PyEMTG.Solution path\to\mission.emtg --output mission.dspkg `
  --artifact options=path\to\mission.emtgopt
```

The exporter only writes the package file. It does not import DeepSpace
Storage, open a database, or make network requests. Publish the result
separately:

```powershell
deepspace-storage ingest mission.dspkg `
  --api-url http://127.0.0.1:8765
```

This boundary keeps EMTG deterministic and usable offline while allowing the
same package to be uploaded by EMTG Studio, target orchestration, a build
worker, or a simple command-line script.

Feasibility-atlas evaluations place `family_id`, `sample_key`, branch,
coordinates, and comparison context in `manifest.json` under
`metadata.family`. Publishers can use
`PyEMTG.Solution.family_resource_payload()` and
`family_membership_payload()` to send the corresponding operational family and
sample directly to DeepSpace Storage without an EMTG-specific storage adapter.
