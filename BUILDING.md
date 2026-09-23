# Building EMTG

The primary release target is the Windows x64 IPOPT-only command-line
application. Linux packaging is experimental. This community candidate still
requires fresh-machine qualification; see [SUPPORT.md](SUPPORT.md).
SNOPT remains available to licensed users as an opt-in local backend.

## Windows

On a new machine, follow [Windows setup](docs/1_Developers/windows_setup.md)
once for pinned portable tools and the Visual Studio workload command.
Preflight reports all missing prerequisites together.

From PowerShell in the repository root:

```powershell
.\build.ps1
```

If the machine enforces a restrictive PowerShell execution policy, use the
equivalent one-command wrapper: `.\build.cmd`.

Use short checkout and mission-output paths. Nested native hardware/model paths
can exceed the Windows path limit even when the Python caller can access them.

The script bootstraps pinned dependencies under `_local`, builds and tests the
release, audits the EXE's runtime imports, and writes the standalone EXE and
portable ZIP to `dist`. Use `-Offline` after one successful online build.

For complete qualification, install `requirements-qualification.txt` and run:

```powershell
.\scripts\qualify-windows.ps1
```

With a restrictive PowerShell execution policy, use `.\qualify.cmd`.

This includes Python, fast/native C++, bounded trajectories, all four AEPS
cases, package relocation, audits, and a cached offline rebuild. Use
`-SkipBuild` with an already rebuilt release, or `-Fast` for Python and fast
C++ tests after the first managed build. Logs, JUnit and separate pass/fail/skip
counts go into a fresh short `_local/qNNNN` directory. AEPS keeps its
1200-second budgets and uses up to four workers (`-Workers 1` is sequential).
Optional NASA/GUI workflows remain separate.

## Linux (experimental)

The managed Linux build, CTest suite, dependency audit and relocation have
been exercised in an Ubuntu 22.04 x64 container. Artifacts remain labeled
`experimental`; other distributions and architectures require qualification.

On Ubuntu 22.04 or newer, install the prerequisites once with `--bootstrap`,
then use the normal one-command build:

```bash
./build.sh --bootstrap
./build.sh
```

`--bootstrap` installs the base compiler tools with `apt`; omit it when they are
already present. The experimental portable tarball is written to `dist`.

## Fast development tests

The dependency-light suite does not build the full optimizer:

On Windows, first initialize the compiler downloaded by the managed build:

```powershell
. .\scripts\windows-environment.ps1
Initialize-EmtgLocalTools
Initialize-EmtgMingw
```

```text
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast
```

Machine-local dependency hints may still be supplied as ordinary CMake cache
variables, but `EMTG-Config.cmake` is deprecated and is not used by release CI.

If this preset was previously configured with another compiler, add `--fresh`
to its configure command.

## Complete bootstrap prerequisites

Windows requires PowerShell 5.1+, Python 3.10+, Git on PATH, CMake 3.25+, Ninja 1.10+,
and Visual Studio 2022 Build Tools with the C++ tools and Windows SDK. The
managed build uses MinGW-w64; Visual Studio supplies bootstrap and DLL-audit
tools. Internet access is needed for the first build. `-Offline` requires the
entire cached dependency graph and compiler and does not provision them.

Ubuntu bootstrap requires sudo and network access. It installs GCC, GFortran,
Git, Ninja, Autoconf/Automake, Libtool, curl, zip/unzip, tar, pkg-config,
CA certificates and Python venv,
then provisions CMake 3.31.6 in `_local/tools/cmake`. Ubuntu's stock CMake is
not assumed to meet the 3.25 preset minimum. Subsequent runs use that venv.

Both wrappers verify vcpkg HEAD against `cmake/vcpkg-revision.txt` and reject
modified tracked vcpkg sources. A custom `VCPKG_ROOT` must satisfy the same
revision check. Release evidence includes `dist/build-toolchain.txt` with the
exact compiler/tool versions and installed vcpkg graph, plus the SPDX report.
The managed graph uses IPOPT 3.14.11; the separate Linux IPOPT CI graph uses
3.14.19. Passing either graph does not qualify the other.

Optional Propulator and PyHardware are disabled by release presets. Enable
them in a separate developer build with `EMTG_BUILD_PROPULATOR=ON` and
`EMTG_BUILD_PYHARDWARE=ON` when their Python/Boost.Python dependencies exist.
The legacy wxPython interface similarly needs a separately installed wxPython.
These optional configurations require their own qualification evidence.

GNU/Clang release builds map compiler-recorded source paths to relative names
using [GCC's file-prefix mapping](https://gcc.gnu.org/onlinedocs/gcc-9.1.0/gcc/Overall-Options.html).
The managed LAPACK overlay compiles Fortran sources using relative paths because
Fortran runtime diagnostics do not consistently honor prefix maps. EMTG debug
builds retain local paths for debugging. The build wrappers audit the actual archives,
executables, and release metadata with `scripts/audit-release-paths.py`; cached
dependencies built before this change may require an online rebuild. The audit
always rejects the supplied source/cache roots. Five byte-identical NASA example
assets retain their upstream installation placeholders and Office metadata;
their exact hashes are documented in the audit script. Four exact native option
default strings retain NASA's generic installation placeholders. Neither exemption
allows the supplied source/cache roots. Scientific files are never silently
rewritten during packaging.
