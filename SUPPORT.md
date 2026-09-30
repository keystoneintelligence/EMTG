# Support and compatibility

EMTG Community Edition has passed the public Windows/Linux qualification described
below. Each release retains evidence for its actual source and packaged binaries.
The tested configurations do not establish support for every compiler, operating
system, or mission.

## Configuration matrix

| Configuration | Scope and status |
| --- | --- |
| Windows x64, managed IPOPT CLI | Primary CLI release target. Managed MinGW builds passed on hosted Windows 2022, including Python 3.12, CTest, bounded native IPOPT and AEPS cases, extracted-bundle execution, dependency/path audits, and offline rebuild identity. Local clean-build evidence is also retained. |
| Ubuntu 22.04 x64, managed IPOPT CLI | Experimental. The actual managed dependency graph passed on hosted Ubuntu 22.04 x64, including build, CTest, dependency/path audits, packaging, and extracted-bundle relocation. A separate Linux solver workflow has passed its analytic and AEPS gates with its own IPOPT version. |
| Other Linux distributions or architectures | Unqualified. Ubuntu results do not establish support elsewhere. |
| macOS | Unqualified. Some dependency-discovery code exists; no working build or release artifact is promised. |
| SNOPT | Optional source-build backend for licensed users. Excluded from public bundles. Licensed NASA/SNOPT execution remains a separate qualification. |
| `PyEMTG.Results` and option helpers | Independent Python interfaces covered by regression tests. They do not require the historical GUI. |
| OuterLoop | Optional generic search, continuation, and computational caches. Caller applications own external translation, publication, and mission policy. |
| Propulator and PyHardware | Optional developer configurations requiring Python/Boost.Python dependencies. Not included in the managed CLI release qualification. |
| Historical wxPython GUI | Retained source with separate dependencies. No current GUI dependency combination is qualified by the CLI or Results tests. |

## Scientific compatibility

IPOPT is the default solver. Existing mission files should set
`NLP_solver_type` explicitly when their intended backend matters. The general
feasibility tolerance is `1e-5`; dedicated IPOPT qualifications explicitly use
`1e-8`. Solver convergence tolerances are distinct from physical-output acceptance
envelopes. Neither is loosened to make a regression pass.

Native options and output formats are retained, with explicit validation for
NASA-compatible option export. Export requires an explicit compatible solver and
rejects unsupported active fork settings. It does not establish that every fork
configuration can run in NASA EMTG, or that IPOPT and SNOPT produce identical
trajectories. Historical NASA workflows require their own scientific evidence.

Fixed-step integration remains the default. Adaptive integration and optional
derivative, spline, or caching configurations require appropriate numerical and
mission-level qualification; a passing parser or build test is insufficient.

The managed dependency graph uses IPOPT 3.14.11. The separate Linux solver CI
graph uses IPOPT 3.14.19. Other providers and versions require their own checks.
Record the exact toolchain, solver, options, and SPICE kernel hashes with results.

## Known operating limits

- Large BSP kernels are external inputs. A bundle without them can execute
  `--doctor` but is not ready to solve an arbitrary mission.
- Windows native file I/O can fail when nested mission paths exceed the platform
  path limit. Use short checkout, temporary, and output directories.
- GUI dependencies, Python extension ABIs, and custom solver providers are not
  supplied or qualified by the portable CLI bundle.

See [qualification scope](docs/1_Developers/qualification.md) for development
evidence and remaining gates. Release readiness is tracked using the
[release checklist](docs/1_Developers/releasing.md).

The [upstream compatibility review](docs/1_Developers/upstream_compatibility_review.md)
records the restored Windows Python-extension/SNOPT runtime packaging behavior
and low-level C++ tolerance default, with focused regression evidence. Complete
optional-workflow qualification remains separate from the public IPOPT CLI scope.

## Getting help

Report fork-specific problems through
[GitHub issues](https://github.com/keystoneintelligence/EMTG/issues). Include the
commit or release version, operating system and architecture, build command,
solver/provider versions, `--capabilities` output, and a small reproducer you
can share. Review logs and mission files for private paths or data before posting.
Support and response times depend on maintainer availability.
