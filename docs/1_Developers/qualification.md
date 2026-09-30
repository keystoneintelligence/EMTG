# Qualification scope

EMTG Community Edition retains qualification evidence for the actual code and
artifacts tested. New releases build and qualify the matching tag; earlier
artifacts are not relabeled as binaries from a later revision. See
[SUPPORT.md](../../SUPPORT.md) and the [release checklist](releasing.md).

## Public Community Edition evidence

The complete [managed release workflow, run 36510594625](https://github.com/keystoneintelligence/EMTG/actions/runs/36510594625)
passed at commit `ab20f6d8a4cc1aa6be149b469f77e14753e0abcd`. Its source tree is
identical to the shipped merge commit `8ab91cc3942e57c7b0acd2471863f8c2c2945f3c`.
Artifacts retain the actual build revision in their receipts.

| Check | Recorded scope |
| --- | --- |
| Hosted Windows managed qualification | Python 3.12: 271 passed, three optional skips; seven fast and 15 managed CTest cases; eight bounded native IPOPT cases; four AEPS cases; two extracted-package cases. All required stages passed. |
| Windows distribution | Dependency/path audits, source and packaged-executable receipts, and offline rebuilt-executable identity passed. |
| Hosted Ubuntu 22.04 managed graph | IPOPT 3.14.11 build, CTest, dependency/path audits, packaging, and extracted-bundle relocation passed. Linux remains experimental. |
| Separate Linux solver graph | IPOPT 3.14.19 analytic tests and the opt-in AEPS matrix passed in [run 36456464099](https://github.com/keystoneintelligence/EMTG/actions/runs/36456464099), at `b825dbec76d676a366364f1be67fc1b5505d0ff6`. |
| Merged-head fast checks | Windows/Linux Python, C++/CMake smoke tests and Linux IPOPT analytic regression passed at `622c4ca89eca6fa12449c4a45299ec0d3172d69a`, before its identical-tree merge into the shipped branch. |

These results cover the listed configurations and cases. Skipped optional gates
are not passing results. Consumer applications maintain their own end-to-end
qualification outside public EMTG CI; their results are not a promise made by
this CLI release. Licensed SNOPT, historical GUI, optional Python extensions,
macOS and other platforms require their own evidence.

## Solver and scientific scope

The managed graph uses IPOPT 3.14.11. The separate `IPOPT Open-Source Solver`
workflow uses IPOPT 3.14.19 and system MUMPS; passing one does not qualify the
other. The four-case AEPS matrix remains the opt-in expensive solver gate,
with its original physical acceptance envelopes, explicit `1e-8` NLP
feasibility setting, and 1200-second per-case budget. General options default
to `1e-5`; IPOPT remains solver `2`. Fixed-step integration remains the default;
adaptive integration and other experimental numerical configurations require
separate scientific evidence.

Kernel discovery uses lexical filename order on every platform. SPICE gives
later-loaded overlapping kernels precedence, so retain the selected kernel
inventory and hashes with results. Changing the kernel set can change a
trajectory even when the code and options are identical.

NASA export tests can parse supported output with an unmodified NASA PyEMTG
checkout using `EMTG_NASA_PYEMTG`. Licensed SNOPT execution is a separate gate
and is not claimed by an IPOPT-only build. Preserve the historical testatron
`1e-10` comparison threshold and select `--emtg_solver SNOPT` for that
qualification. Export parsing alone does not establish complete NASA mission
compatibility or equal optima between solvers.

Propulator and PyHardware require matching Python/Boost.Python toolchains.
On Windows their optional build targets produce `.pyd` modules. Licensed Windows
SNOPT builds stage the legacy `lib/snopt.dll` or configuration-specific
`build/libsnopt.dll` runtime beside the executable and install it in `bin`.
For a custom layout, set `EMTG_SNOPT_RUNTIME_DLL` to the existing DLL. Static
builds need no DLL, and SNOPT-disabled builds never stage one. Additional vendor
runtime dependencies remain the licensed developer's responsibility.
The historical GUI needs a separately qualified wxPython environment. These
are not part of the managed CLI release qualification. macOS and other Linux
platforms or architectures remain unqualified.

## Commands and workflows

`Fast Tests` runs the public Python and portable C++ checks.
`Build Release Packages` builds the managed graph, runs CTest and dependency
and path audits, and verifies extracted bundles. Its manual dispatch can
produce candidate artifacts; its matching-tag path prepares a qualified draft
for final maintainer publication review.
`IPOPT Open-Source Solver` exercises the separate Linux solver graph; enable
its `run_aeps_atlas_qualification` input to run the AEPS matrix.

The bounded native gates use the [small checked-in regression ephemeris](../../tests/fixtures/ephemeris/README.md).
Stage it with `python scripts/prepare_test_ephemeris.py --output _local/test-universe`
and set `EMTG_TEST_UNIVERSE` to that directory's absolute path. This replaces the
large development BSP inventory for these tests only. The public IPOPT workflow
does this automatically for its AEPS opt-in; it requires no private asset transfer.

After staging the candidate executable as `bin/EMTGv9.exe` (the existing test
harness path on either platform), select the native gates from the source root:

```text
python -m pytest tests/test_outerloop_emtg_integration.py -k "not aeps_real_atlas"
python -m pytest tests/test_outerloop_emtg_integration.py::test_aeps_real_atlas_baseline_and_nearby_hardware_matrix
```

Set `EMTG_RUN_OUTERLOOP_INTEGRATION=1` for both commands and
`EMTG_ATLAS_AEPS_BUDGET_SECONDS=1200` for the matrix. Use a short `--basetemp`
on Windows. Preserve the output and distinguish missing-asset, execution,
scientific-acceptance, and intentionally skipped results. Do not shorten the
matrix budget or change tolerances to obtain a passing release result.

Release approval needs the actual matching-source logs and artifacts. Keeping
these commands or workflow files in the repository does not complete that gate.


## Artifact identity and the qualification interpreter

All Python release qualification uses CPython 3.12. Use a dedicated virtual
environment with requirements-qualification.txt; runtime consumers may choose
their own compatible dependencies. Qualification does not change mission
tolerances or claim support for an untested interpreter.

Managed builds write a platform-specific EMTG-*.provenance.json beside release
artifacts. It binds the source commit/tree, source cleanliness, executable,
archive, toolchain inventory and dependency notices/SBOM. Custom dirty builds
remain possible, but their receipt cannot qualify an official release.
Keep the receipt with its artifact directory.

Before native tests, qualify-windows.ps1 verifies the build EXE and distribution.
It retains the exact tested distribution under the new run's qualified-release
directory, checks the extracted EXE, and directs the offline rebuild elsewhere.
The rebuilt EXE must match; different bytes require new qualification.
The previous local bin/EMTGv9.exe is restored after testing.

-SkipBuild requires existing matching provenance and clean test source.
source-commit.txt from older runs alone is not binary provenance. Rebuild older
artifacts to obtain current release evidence; do not relabel them.

Artifact-run rechecks validate the selected GitHub run against the receipt and
record build and test revisions separately. Different revisions require the
explicit allow_source_comparison workflow input and produce comparison evidence,
not qualification of a new binary. A recheck does not publish a release.
Requested native and package stages reject missing, skipped or incomplete JUnit
evidence. Optional tests in the ordinary Python suite remain reported as skips.
