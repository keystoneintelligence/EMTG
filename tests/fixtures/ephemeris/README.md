# Native regression ephemeris

`emtg-tests-v1.bsp.gz` is an ordinary Git fixture: 18,837,399 bytes compressed,
20,392,960 bytes expanded, replacing a 1,719,456,768-byte development input set
for the bounded Earth–Mars and four-case AEPS tests. A fresh clone contains it;
no LFS, credentials, private repository, or scientific-data download is needed
to run these tests. The release executable must still be built separately.

This is test data with deliberately limited bodies and time coverage. It is
not installed into portable release bundles and is not a general mission kernel.
Other missions, the full historical testatron suite, and consumer applications
must supply their own appropriate ephemerides.

## Run the existing native tests

From the source root, stage a NEW short directory:

```text
python scripts/prepare_test_ephemeris.py --output _local/test-universe
```

The standard-library script verifies compressed and expanded SHA256 hashes,
copies the ordinary test universe files, and adds only this fixture's BSP.
It refuses to overwrite an existing universe. It does not modify production
kernels, native mission files, or the packaged runtime data.

Set these environment variables in PowerShell:

```powershell
$env:EMTG_TEST_UNIVERSE = (Resolve-Path _local/test-universe).Path
$env:EMTG_RUN_OUTERLOOP_INTEGRATION = '1'
$env:EMTG_ATLAS_AEPS_BUDGET_SECONDS = '1200'
```

Or in Bash:

```bash
export EMTG_TEST_UNIVERSE="$PWD/_local/test-universe"
export EMTG_RUN_OUTERLOOP_INTEGRATION=1
export EMTG_ATLAS_AEPS_BUDGET_SECONDS=1200
```

Copy the newly built candidate executable to `bin/EMTGv9.exe` (also the harness
name on Linux; make it executable), then run:

```text
python -m pytest tests/test_outerloop_emtg_integration.py -k "not aeps_real_atlas" --basetemp _local/nb
python -m pytest tests/test_outerloop_emtg_integration.py::test_aeps_real_atlas_baseline_and_nearby_hardware_matrix --basetemp _local/na
```

Keep Windows checkout and output paths short. Save each basetemp directory
before a rerun, because pytest can clear it. Expect eight passes in the bounded
selection (including preflight) and four in the AEPS matrix, with no opt-in
skips. Solver settings, 1200-second AEPS budgets and scientific assertions are
unchanged. The public IPOPT workflow stages this same fixture for its AEPS opt-in.

## Provenance and maintenance

The sources are JPL planetary/satellite SPKs and the saved small-body SPK
aggregate identified by SHA256 in `recipe.json`. Only target 20136163 is retained
from that aggregate. See [NAIF kernel data](https://naif.jpl.nasa.gov/naif/data.html)
and [JPL Horizons](https://ssd.jpl.nasa.gov/horizons/) for the source services.
Freshly generated Horizons data can change; a new query is not the recorded
baseline. Regeneration requires the exact source hashes in the recipe.

Extraction uses [NAIF spksub](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/cspice/spksub_c.html)
to copy original records, without resampling or fitting new polynomials.
Later-loaded files and later segments retain precedence, including Sun/Earth
segments supplied by satellite kernels. Unused bodies and superseded segments
are omitted. Source comments and segment labels containing unrelated machine
metadata are excluded; scientific records are unchanged.

The recipe retains the body-center dependency chains, universe reference epochs,
derivative margins, and the full spline-initialization ranges of the existing
tests. EMTG initializes those spline tables even in the SPICE test configuration;
cropping only to the mission trajectory dates is insufficient.

For fixture maintainers with the recorded source files and SpiceyPy 8.0.2 /
CSPICE N0067:

```text
python scripts/build_test_ephemeris.py --sources SOURCE-DIRECTORY --output NEW-FIXTURE.bsp
python scripts/verify_test_ephemeris.py --sources SOURCE-DIRECTORY --kernel NEW-FIXTURE.bsp
```

The generator refuses existing outputs and checks every source hash. Two local
generations produced the same kernel SHA256. The verifier compares 13,468 states
with respect to the Sun and solar-system barycenter, including segment endpoints
and their one-second neighborhoods; all comparisons were bit-identical under
the recorded toolkit. Missing states fail verification. This comparison supports
the bounded fixture; it does not qualify arbitrary missions or fresh builds.

`manifest.json` records the exact kernel/archive hashes and tool versions. Gzip
uses no source filename and a zero timestamp. Retain source attribution and this
recipe when redistributing the test fixture. Keep raw execution logs separate
from public artifacts because logs can contain local paths.
