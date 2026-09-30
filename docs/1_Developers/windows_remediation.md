# Fresh Windows build remediation

Baseline: `293aaca3951c0b42d12e107a41c87b2729057508`, Windows 11 x64,
Ryzen 7 6800H. The original release built and solved the bounded and AEPS
cases, but its Python suite had three failures. The remediation keeps mission
formats, production NSGA-II, IPOPT tolerances and scientific acceptance limits.

| Finding | Focused correction | Acceptance evidence |
| --- | --- | --- |
| Host-specialized OpenBLAS | Dynamic CPU dispatch; fixed CORE2 common code | Original binary fails on AVX `vmovsd` under Nehalem SDE; extracted rebuilt solver completes the mission at 5.14e-9 violation |
| SQLite handles prevent Windows cleanup | Transaction context always closes; explicit transaction API retained | CLI workflow plus commit/rollback/delete regression |
| NumPy matrix-to-scalar conversion | Explicit scalar indexing for position, velocity and angle | Existing real seed converter test on both NumPy generations |
| Python 3.12 changes seeded ZDT path | Explicit historical summation order in benchmark only | Unchanged seed, generations, IGD and hypervolume thresholds |
| Missing/manual prerequisites | Versioned local setup recipe; aggregated preflight | Fresh portable Python installation and missing-tool simulation |
| Fast tests cannot discover compiler | Shared session-only compiler setup | Ordinary PowerShell fast configure/build/CTest |
| Shared VS/SDK state | Explicit containment exceptions and provenance recipe | Installer hash/version and documented offline-layout/VM requirement |
| Fragmented qualification | One command with complete native logs, JUnit and separate skips | Fast/full command paths and branch CI |
| Plot test silently skipped | Matplotlib in pinned qualification set | Existing plotting test runs |
| LAPACK unsupported-feature warning | MinGW support predicate corrected; bypass removed | Managed graph installs without `--allow-unsupported` |
| CoinUtils LAPACK probe warning | Explicit static LAPACK/BLAS/Fortran linkage on MinGW | `dsyev` configure probe succeeds |
| Four native warnings | Numeric zero; shared spherical-state predicate | Native build and both spherical representation regression cases |

Windows Python CI: **240 passed, 13 skipped** on both
Python 3.12.10 / NumPy 2.5.3 and Python 3.10.11 / NumPy 1.26.4.
The seven fast C++ tests and all 15 release CTests pass. Skips include opt-in native/package tests,
which the full qualification runs separately, and external NASA checks.

Linux Python 3.10.21 and 3.12.14 CI each report **235 passed, 18 skipped**;
the five additional skips are Windows-only launcher/stream/lock/preflight regressions.
All seven [fast CI jobs](https://github.com/keystoneintelligence/EMTG/actions/runs/35834826089)
pass. Windows CI exposed an additional Fortran probe discrepancy: the probe
was unstripped while managed releases use `-s`. Applying the release linker
setting fixes both Windows jobs with the existing path assertions intact.
The newly built BLAS also passes a 64x64 DGEMM calculation under Nehalem SDE
and reports the Nehalem runtime kernel. The offline cache guard accepts the
new CORE2/dynamic-arch metadata and rejects old ZEN cache metadata.
The extracted solver's non-AVX Earth-Mars trajectory passes independent
coast, SPICE endpoint and rocket-equation checks with the original tolerances.

A repeat build exposed an additional cache issue: the standard MinGW triplet
hashed the full session PATH, invalidating every dependency when switching
PowerShell sessions. The overlay passes PATH without hashing it; vcpkg still
tracks the compiler, tools, port recipes and build options. The interrupted
redundant build is retained as diagnostic evidence, not counted as a test pass.

Repeated environment initialization also duplicated PATH entries. Deduplication
now preserves search order and keeps PATH stable across three consecutive
initializations in PowerShell 5.1 and 7, with every managed tool still resolved.

The full batch entry point exposed inherited PowerShell 7 module paths inside
Windows PowerShell 5.1: packaging could not discover `Get-FileHash`. Both `.cmd`
launchers now clear `PSModulePath` within `setlocal`, so the child reconstructs
its own module paths. Two launcher regressions fail with a conflicting inherited
module before the fix and pass afterward. CI invokes the same `qualify.cmd`.

Windows PowerShell also wrapped a harmless CMake stderr warning as an error,
incorrectly failing fast-test setup. The qualification capture now logs native
stderr and preserves exit codes. A regression verifies warning/exit zero,
nonzero native exit, and genuine PowerShell failure separately.

The CPU-check helper also rejects missing SDE, executable or options files
before loading EMTG defaults. A missing-options diagnostic fails clearly,
and the final valid-input run reproduces the non-AVX feasibility result.

Preflight also handles an installed-but-unusable tool, such as a broken Windows
App Execution Alias. A failed native version query previously stopped Windows
PowerShell at the first tool. Each query now records its failure and continues,
so all missing or unusable prerequisites are reported together. The diagnostic
and regression exercise failing CMake, Ninja and Python commands on both
supported Python versions, preserving the caller's error preference.
The subsequent normal fast qualification (`q0009`) passes 240 Python tests
(3 skips, 10 integration cases deselected) and all seven C++ tests.

CI exposed a Windows cache-lock race: the CRT can report permission denied
while a competing lock-file deletion is pending. Acquisition now retries
within the existing deadline; permanent denial still propagates. The original
failure reproduced after 452 acquisitions; 8,000 contended acquisitions pass
afterward, along with a deterministic regression on both Python versions.

Release CI now retains native mission outputs and saves compiled dependencies
before trajectory qualification. vcpkg checks each cached package's ABI key.
A manual workflow option rechecks all four unchanged AEPS cases against an
existing release artifact, without repeating the dependency build.

The first fresh runner consuming that binary cache exposed a compiler-bootstrap
issue: `vcpkg-gfortran` restored runtime DLLs but did not acquire the compiler.
Only that bootstrap invocation now disables binary caching, so its port runs
and downloads the pinned compiler; the main library graph still uses its cache.
The complete corrected workflow is exercised by this
[Windows release CI run](https://github.com/keystoneintelligence/EMTG/actions/runs/35834826128).
The cold cache-producing build took 126 minutes 8 seconds; the overall CI job
allows four hours while retaining the 1,200-second per-mission limits.

The final local full command (`q0008`, implementation `6276a99f`) passes all
13 stages: 239 Python tests (3 skips), 7 fast CTests, 15 release CTests,
8 bounded native cases, all 4 AEPS cases, both extracted-package tests,
DLL/path audits and the cached offline rebuild. Separate overlapping suites
must not be summed. All 46 ZIP files match the tested extraction.

AEPS baseline repeats deliver 1,685.852694 kg with 7.871800e-9 violation;
nearby repeats deliver 1,686.719710 kg with 3.689179e-9 violation. The original
1,200-second budgets, mass threshold and feasibility assertions are unchanged.
The cached offline build takes 38 seconds; the first fresh Windows CI native
build, tests and packaging took 96 minutes. That CI run's nearby AEPS failures
were corrected by reducing CI concurrency from four workers to two. The same
package passes all four cases on the evaluated two-core/four-thread runner in
40 minutes ([recheck](https://github.com/keystoneintelligence/EMTG/actions/runs/35823371657)).
Its baseline/nearby delivered masses are 1,439.764306/1,685.442440 kg, with
violations 8.101408e-9/9.927986e-9. Runtime dispatch and a fixed wall-clock
budget can produce different feasible solutions on different CPUs.
The exact CI executable also passes the nearby case locally with the local
metrics. No solver budget, tolerance, hardware mutation or acceptance limit
was changed; only release-CI scheduling uses `-Workers 2`.

A configuration change by itself is not CPU qualification.
See [Windows setup](windows_setup.md) for reproducible commands and
`_local/remediation` / `_local/qNNNN` for this evaluation's retained evidence.
