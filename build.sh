#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
local_dir="$root/_local"
dist="$root/dist"
vcpkg="${VCPKG_ROOT:-$local_dir/tools/vcpkg}"
version="$(tr -d '[:space:]' < "$root/VERSION")"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Invalid EMTG version in VERSION: '$version'" >&2; exit 2; }
offline=0
skip_tests=0

printf '%s\n' \
  'WARNING: EMTG Linux artifacts are experimental and have not been validated on a clean Linux host.' >&2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) offline=1 ;;
    --skip-tests) skip_tests=1 ;;
    --bootstrap)
      sudo apt-get update
      sudo apt-get install -y build-essential cmake curl gfortran git ninja-build pkg-config zip
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

mkdir -p "$local_dir" "$dist"

preset=linux-release
if [[ ! -d "$vcpkg/.git" ]]; then
  [[ $offline -eq 0 ]] || { echo "Offline build requested but vcpkg is missing" >&2; exit 3; }
  git clone --branch 2025.06.13 --depth 1 https://github.com/microsoft/vcpkg.git "$vcpkg"
fi
if [[ ! -x "$vcpkg/vcpkg" ]]; then
  [[ $offline -eq 0 ]] || { echo "Offline build requested but vcpkg is not bootstrapped" >&2; exit 3; }
  "$vcpkg/bootstrap-vcpkg.sh" -disableMetrics
fi
export VCPKG_ROOT="$vcpkg"
mkdir -p "$local_dir/vcpkg-cache"
export VCPKG_BINARY_SOURCES="clear;files,$local_dir/vcpkg-cache,readwrite"
installed="$local_dir/builds/linux-release/vcpkg_installed/emtg-x64-linux-static"
if [[ $offline -eq 1 ]]; then
  required=(
    "$installed/include/boost/version.hpp"
    "$installed/include/coin-or/IpStdCInterface.h"
    "$installed/include/cspice/SpiceUsr.h"
    "$installed/lib/libipopt.a"
    "$installed/lib/libcoinmumps.a"
    "$installed/lib/libcspice.a"
    "$installed/lib/liblapack.a"
    "$installed/lib/libopenblas.a"
  )
  missing=()
  for asset in "${required[@]}"; do
    [[ -e "$asset" ]] || missing+=("$asset")
  done
  if [[ ${#missing[@]} -gt 0 ]]; then
    printf 'Offline build cache is incomplete. Missing:\n' >&2
    printf '%s\n' "${missing[@]}" >&2
    exit 3
  fi
else
  "$vcpkg/vcpkg" install \
    --triplet emtg-x64-linux-static \
    "--x-manifest-root=$root" \
    "--x-install-root=$local_dir/builds/linux-release/vcpkg_installed" \
    "--overlay-ports=$root/cmake/vcpkg-overlays" \
    "--overlay-triplets=$root/cmake/vcpkg-triplets"
fi

if [[ $skip_tests -eq 1 ]]; then
  cmake --preset "$preset"
  cmake --build --preset "$preset" --target EMTGv9
  cpack --preset "$preset"
else
  cmake --workflow --preset "$preset"
fi

build="$local_dir/builds/$preset"
executable="$build/bin/EMTGv9"
[[ -x "$executable" ]] || { echo "Expected executable was not produced: $executable" >&2; exit 1; }
reported_version="$("$executable" --version)"
[[ "$reported_version" == "EMTG $version" ]] || {
  echo "Built executable reports '$reported_version', expected 'EMTG $version'" >&2
  exit 1
}
bash "$root/scripts/audit-linux-dependencies.sh" "$executable"
cp "$executable" "$dist/EMTGv9-linux-x64-experimental"
sha256sum "$dist/EMTGv9-linux-x64-experimental" > "$dist/EMTGv9-linux-x64-experimental.sha256"
cp "$root/packaging/linux/EXPERIMENTAL.md" "$dist/LINUX-EXPERIMENTAL.txt"
find "$build" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.sha256' \) -exec cp {} "$dist/" \;
cmake \
  "-DSTATUS_FILE=$local_dir/builds/linux-release/vcpkg_installed/vcpkg/status" \
  "-DOUTPUT=$dist/EMTG-linux-x64-experimental.spdx" \
  "-DEMTG_VERSION=$version" \
  -DPLATFORM=linux-x64-experimental \
  -P "$root/cmake/GenerateVcpkgSbom.cmake"

echo "EMTG artifacts: $dist"
