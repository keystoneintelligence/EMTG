# Fresh Windows build remediation

Baseline: `293aaca3951c0b42d12e107a41c87b2729057508`, Windows 11 x64,
Ryzen 7 6800H. The original release built and solved the bounded and AEPS
cases, but its Python suite had three failures. The remediation keeps mission
formats, production NSGA-II, IPOPT tolerances and scientific acceptance limits.

| Finding | Focused correction | Acceptance evidence |
| --- | --- | --- |
| Host-specialized OpenBLAS | Dynamic CPU dispatch; fixed CORE2 common code | Original binary fails on AVX `vmovsd` under Nehalem SDE; rebuilt solver must pass the same mission |
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

Local Python qualification after the fixes: **235 passed, 13 skipped** on
both Python 3.12.10 / NumPy 2.5.3 and Python 3.10.11 / NumPy 1.26.4.
The seven fast C++ tests pass. Skips include opt-in native/package tests,
which the full qualification runs separately, and external NASA checks.

Complete release, non-AVX, AEPS and remote-CI results are recorded when their
runs finish. A configuration change by itself is not CPU qualification.
See [Windows setup](windows_setup.md) for reproducible commands and
`_local/remediation` / `_local/qNNNN` for this evaluation's retained evidence.
