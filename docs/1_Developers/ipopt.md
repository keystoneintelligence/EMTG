# IPOPT backend

IPOPT is EMTG's default nonlinear-programming backend and is the supported
fully open-source path. SNOPT remains optional for licensed users. EMTG targets
IPOPT 3.14 and newer through `IpStdCInterface.h`; CI verifies the Linux
IPOPT-only configuration. The Windows instructions below are exercised with
IPOPT 3.14.20. macOS discovery is implemented but has not been built in this
repository's current validation environment.

## Install and discover IPOPT

EMTG tries an installation-provided CMake config first, then `pkg-config`, then
explicit CMake hints. Any one of these forms is sufficient:

```text
-DIPOPT_ROOT_DIR=/path/to/ipopt/prefix
```

or:

```text
-DIPOPT_INCLUDE_DIR=/path/containing/IpStdCInterface.h
-DIPOPT_LIBRARY=/path/to/libipopt.so
```

Windows DLL installations may also specify:

```text
-DIPOPT_BIN_DIR=C:/path/to/ipopt/bin
-DIPOPT_LIBRARY_DIR=C:/path/to/ipopt/lib
-DIPOPT_MINGW_BIN_DIR=C:/path/to/mingw/runtime/bin
```

`IPOPT_RUNTIME_LIBRARY` and `IPOPT_LIBRARY` accept exact files when a prefix is
not laid out conventionally. `IPOPTDIR_OVRD` remains a compatibility alias for
older EMTG scripts.

On Linux, install IPOPT development headers and a sparse linear solver through
the system package manager. On macOS, install an IPOPT package that exposes a
CMake config, `pkg-config` file, or conventional `include` and `lib`
directories. A source build following IPOPT's own `INSTALL.md` is also valid.

For a MinGW-built IPOPT DLL consumed by MSVC, EMTG verifies the required C API
exports with `dumpbin` and generates a small MSVC import library in
`<build>/ipopt_bridge`. No `.def` file or binary from the source tree is used.
The Visual Studio developer environment must provide `dumpbin.exe` and
`lib.exe`. Runtime DLLs from the IPOPT and optional MinGW runtime directories
are copied next to `EMTGv9.exe` after a successful build.

## Configure and build

The reusable IPOPT-only preset builds the full executable and analytic solver
tests:

```powershell
$env:IPOPT_ROOT_DIR = 'C:/path/to/ipopt'
cmake --preset ipopt-only
cmake --build --preset ipopt-only --target EMTGv9 ipopt_interface_tests
ctest --preset ipopt-only -R ipopt
```

The equivalent explicit configuration is:

```text
cmake -S . -B _local/builds/ipopt-only -G Ninja \
  -DENABLE_SNOPT=OFF -DENABLE_IPOPT=ON \
  -DIPOPT_ROOT_DIR=/path/to/ipopt \
  -DBUILD_EMTG_TESTBED=ON -DRUN_IPOPT_INTERFACE_TESTBED=ON
```

`EMTG-Config.cmake` is optional. A clean checkout can be configured entirely
with cache variables, presets, environment hints, and standard packages.
Configuration fails early if the C header, link library, Windows runtime, C API
exports, or MSVC bridge tools are missing.

To build both solvers, enable both flags and provide both dependencies. A
requested solver that is not compiled into the executable is an error; EMTG
does not silently substitute another backend.

## Select the solver

IPOPT is the default for newly created options. Its stable numeric ID is `2`:

```text
NLP_solver_type 2
```

SNOPT remains ID `0`. ID `1` is reserved for legacy WORHP files but is
unsupported and is not offered by PyEMTG. Existing files that intentionally
use SNOPT should keep an explicit `NLP_solver_type 0`; files that omitted the
old SNOPT default now select IPOPT. This is the deliberate migration that makes
a default open-source build runnable.

The build writes `bin/solver_capabilities.json`. PyEMTG uses that file (or the
`EMTG_AVAILABLE_NLP_SOLVERS` environment override) to show only solvers in the
current executable. The legacy `snopt_*` option names remain accepted for file
compatibility, but feasibility tolerance, optimality tolerance, major iteration
limit, and maximum run time are applied to either backend.

## Solver semantics and limitations

- EMTG scales decision variables before calling IPOPT and disables IPOPT's
  additional NLP scaling so objective, constraint, and derivative meanings
  remain consistent with the solver abstraction.
- The objective is EMTG function row zero and is excluded from IPOPT's
  constraint vector. Repeated linear/nonlinear Jacobian entries at the same
  row and column are summed before being passed to IPOPT.
- IPOPT uses its limited-memory Hessian approximation. EMTG does not provide
  exact second derivatives.
- `snopt_major_iterations` maps to IPOPT `max_iter`; `snopt_max_run_time` maps
  to `max_wall_time` and is backed by EMTG's intermediate callback. SNOPT minor
  iterations and `NLP_max_step` have no exact IPOPT equivalent and are not
  applied by the IPOPT adapter.
- Solver success is not sufficient on its own: EMTG recomputes decision-vector
  and constraint feasibility for the final or chaperoned point. Acceptable and
  goal-stop exits are accepted only when that returned point satisfies EMTG's
  feasibility tolerance.
- All exceptions and non-finite values are contained at the C callback boundary
  and reported as failed solves. Zero-constraint problems are supported;
  zero-variable or missing-objective problems are rejected during construction.

IPOPT and SNOPT need not return identical decision vectors. Cross-solver
regressions should compare mission feasibility, objective envelopes, event
sequence, time of flight, propellant/final mass, and other physical outputs.

## Tests

Fast, dependency-free checks:

```text
python -m pytest
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast
```

IPOPT adapter and full-build checks:

```text
cmake --preset ipopt-only
cmake --build --preset ipopt-only --target EMTGv9 ipopt_interface_tests
ctest --preset ipopt-only -R ipopt
```

The analytic suite covers selection/unavailable errors, scaled constrained and
unconstrained solutions, objective/constraint/Jacobian callbacks, duplicate
sparsity, caching, status mapping, iteration and goal termination, and
NaN/invalid-scale failures.

The deterministic asteroid mission uses numerical acceptance envelopes:

```text
python testatron/run_asteroid_integration.py --emtg bin/EMTGv9.exe
```

It rejects `FAILURE_` output and checks feasibility, final event/body, final
mass, constraint violation, and ephemeris production. Large SPICE assets remain
a manual/nightly dependency and are not committed as a source fix.

## Troubleshooting

- **Header not found:** point `IPOPT_INCLUDE_DIR` either at the directory that
  contains `IpStdCInterface.h` or at its parent `include` directory.
- **No usable link artifact:** set `IPOPT_LIBRARY` to the exact `.lib`, `.so`,
  `.dylib`, or `.dll.a`. With MSVC and `.dll.a`, also provide the runtime DLL.
- **Missing C API export:** rebuild IPOPT with the standard C interface enabled
  and use IPOPT 3.14 or newer.
- **Windows runtime load error:** put the IPOPT, MUMPS/linear-solver, Fortran,
  BLAS/LAPACK, and MinGW runtime DLLs beside the executable or on `PATH`.
- **Linear solver unavailable:** use an IPOPT provider built with a supported
  sparse linear solver. EMTG does not force a provider-specific solver name.
- **Requested solver unavailable:** inspect `bin/solver_capabilities.json` and
  reconfigure with the corresponding `ENABLE_*` option and dependency.
