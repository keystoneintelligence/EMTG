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
  'WARNING: EMTG Linux artifacts are experimental; current package qualification covers Ubuntu 22.04 x64.' >&2

while [[ $# -gt 0 ]]; do
  case "$1" in
    --offline) offline=1 ;;
    --skip-tests) skip_tests=1 ;;
    --bootstrap)
      sudo apt-get update
      sudo apt-get install -y build-essential autoconf automake autoconf-archive libtool libtool-bin curl gfortran git ninja-build pkg-config zip unzip tar ca-certificates python3-venv
      python3 -m venv "$local_dir/tools/cmake"
      "$local_dir/tools/cmake/bin/python" -m pip install 'cmake==3.31.6' 
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [[ -x "$local_dir/tools/cmake/bin/cmake" ]]; then
  export PATH="$local_dir/tools/cmake/bin:$PATH"
fi
for tool in cmake ctest cpack ninja git g++ gfortran autoreconf automake libtool curl zip unzip tar pkg-config python3; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool. Run ./build.sh --bootstrap on Ubuntu." >&2; exit 2; }
done
cmake_version="$(cmake --version | head -n1 | awk '{print $3}')"
[[ "$(printf '%s\n' 3.25.0 "$cmake_version" | sort -V | head -n1)" == 3.25.0 ]] || {
  echo "CMake >=3.25 is required (found $cmake_version). Run --bootstrap." >&2; exit 2;
}
mkdir -p "$local_dir" "$dist"

preset=linux-release
if [[ ! -d "$vcpkg/.git" ]]; then
  [[ $offline -eq 0 ]] || { echo "Offline build requested but vcpkg is missing" >&2; exit 3; }
  git clone --branch 2025.06.13 --depth 1 https://github.com/microsoft/vcpkg.git "$vcpkg"
fi
expected_vcpkg="$(tr -d '[:space:]' < "$root/cmake/vcpkg-revision.txt")"
actual_vcpkg="$(git -C "$vcpkg" rev-parse HEAD)"
[[ "$actual_vcpkg" == "$expected_vcpkg" ]] || { echo "vcpkg revision mismatch: expected $expected_vcpkg, found $actual_vcpkg" >&2; exit 3; }
git -C "$vcpkg" diff --quiet HEAD -- || { echo "vcpkg tracked sources are modified" >&2; exit 3; }
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

{
  printf 'vcpkg_commit=%s\n' "$actual_vcpkg"
  cmake --version
  ninja --version
  g++ --version
  gfortran --version
  cat "$local_dir/builds/linux-release/vcpkg_installed/vcpkg/status"
} > "$dist/build-toolchain.txt"

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
(cd "$dist" && sha256sum EMTGv9-linux-x64-experimental > EMTGv9-linux-x64-experimental.sha256)
cp "$root/packaging/linux/EXPERIMENTAL.md" "$dist/LINUX-EXPERIMENTAL.txt"
find "$build" -maxdepth 1 -type f \( -name '*.tar.gz' -o -name '*.sha256' \) -exec cp {} "$dist/" \;
cmake \
  "-DSTATUS_FILE=$local_dir/builds/linux-release/vcpkg_installed/vcpkg/status" \
  "-DOUTPUT=$dist/EMTG-linux-x64-experimental.spdx" \
  "-DEMTG_VERSION=$version" \
  -DPLATFORM=linux-x64-experimental \
  -DCOMPILER_RUNTIME_NAME=GNU-GCC-runtime \
  "-DCOMPILER_RUNTIME_VERSION=$(g++ -dumpfullversion -dumpversion)" \
  -P "$root/cmake/GenerateVcpkgSbom.cmake"

python3 "$root/scripts/audit-release-paths.py" "$dist" --forbid-root "$root" --forbid-root "$vcpkg"
echo "EMTG artifacts: $dist"
