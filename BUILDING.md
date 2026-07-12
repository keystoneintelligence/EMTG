# Building EMTG

EMTG's supported distributable configuration is the IPOPT-only command-line
application. SNOPT remains available to licensed users as an opt-in local
backend.

## Windows

From PowerShell in the repository root:

```powershell
.\build.ps1
```

If the machine enforces a restrictive PowerShell execution policy, use the
equivalent one-command wrapper: `.\build.cmd`.

The script bootstraps pinned dependencies under `_local`, builds and tests the
release, audits the EXE's runtime imports, and writes the standalone EXE and
portable ZIP to `dist`. Use `-Offline` after one successful online build.

## Linux (experimental)

Linux packaging has not yet been exercised on a clean Linux host. The wrapper
and package definitions are provided as a best-effort preview, and every
generated Linux artifact is labeled `experimental` until that validation is
completed.

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

```text
cmake --preset ci-fast
cmake --build --preset ci-fast
ctest --preset ci-fast
```

Machine-local dependency hints may still be supplied as ordinary CMake cache
variables, but `EMTG-Config.cmake` is deprecated and is not used by release CI.
