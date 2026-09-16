# Qualification scope

The public community candidate still requires fresh-machine qualification.
The existing development evidence below describes the implementation carried
into the public history; it does not qualify newly built candidate artifacts
or a different machine. See [SUPPORT.md](../../SUPPORT.md) and the
[release checklist](releasing.md).

## Existing development evidence

| Check | Scope previously exercised |
| --- | --- |
| Public Python suite | 221 passed, 13 skipped in the recorded development run. Covers native parsing, artifact inventories, option generation/export, solver selection, generic OuterLoop, and numerical helpers. |
| Managed CTest | 14 passed on provisioned Windows x64 and Ubuntu 22.04 x64. Includes CLI/data discovery, AD, integration, spline/ephemeris, options, IPOPT interface, and deterministic kernel-order checks. |
| Bounded native IPOPT | Eight passed per platform with explicit strict solver settings and unchanged physical acceptance criteria. |
| Package/privacy checks | 17 passed per platform, including extracted-bundle execution and source/cache path detection. Dependency audits passed separately. |

Skipped optional gates are not passing results. The full AEPS matrix was not
rerun for the last documentation/privacy cleanup. New-machine and public-ref
results must identify the exact commit, executable and fixture hashes, solver,
toolchain, platform, and commands. Consumer integration evidence belongs to the
consumer projects and is not a dependency of public EMTG CI.

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
The historical GUI needs a separately qualified wxPython environment. These
are not part of the managed CLI release qualification. macOS and other Linux
platforms or architectures remain unqualified.

## Commands and workflows

`Fast Tests` runs the public Python and portable C++ checks.
`Build Release Packages` builds the managed graph, runs CTest and dependency
and path audits, and verifies extracted bundles. Its manual dispatch can
produce candidate artifacts; its matching-tag path can publish a release.
`IPOPT Open-Source Solver` exercises the separate Linux solver graph; enable
its `run_aeps_atlas_qualification` input to run the AEPS matrix.

After staging the candidate executable as `bin/EMTGv9.exe` (the existing test
harness path on either platform) and providing the scientific assets, the
native gates can be selected from the source root as follows:

```text
python -m pytest tests/test_outerloop_emtg_integration.py -k "not aeps_real_atlas"
python -m pytest tests/test_outerloop_emtg_integration.py::test_aeps_real_atlas_baseline_and_nearby_hardware_matrix
```

Set `EMTG_RUN_OUTERLOOP_INTEGRATION=1` for both commands and
`EMTG_ATLAS_AEPS_BUDGET_SECONDS=1200` for the matrix. Use a short `--basetemp`
on Windows. Preserve the output and distinguish missing-asset, execution,
scientific-acceptance, and intentionally skipped results. Do not shorten the
matrix budget or change tolerances to obtain a passing release result.

Release approval needs the actual fresh-machine logs and artifacts. Keeping
these commands or workflow files in the repository does not complete that gate.
