#!/usr/bin/env bash
set -euo pipefail

executable=${1:?usage: audit-linux-dependencies.sh EXE}
[[ -x "$executable" ]] || { echo "Executable not found: $executable" >&2; exit 2; }

dependencies=$(ldd "$executable")
printf '%s\n' "$dependencies"
if grep -q 'not found' <<<"$dependencies"; then
  echo "Linux release has unresolved shared-library dependencies" >&2
  exit 1
fi
if grep -Eiq 'ipopt|mumps|blas|lapack|gfortran|quadmath|boost|gsl|cspice|libstdc\+\+|libgcc' <<<"$dependencies"; then
  echo "Linux release unexpectedly imports a managed third-party/runtime library" >&2
  exit 1
fi

dynamic=$(readelf -d "$executable")
if grep -Eiq 'RPATH|RUNPATH' <<<"$dynamic"; then
  echo "Linux release contains an RPATH/RUNPATH" >&2
  exit 1
fi

echo "Linux dependency audit passed"
