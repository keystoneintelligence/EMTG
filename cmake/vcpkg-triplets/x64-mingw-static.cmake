# Keep the standard static MinGW graph; only OpenBLAS needs a fixed baseline.
set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
# PATH locates the managed compiler; vcpkg tracks that compiler separately.
# Shell startup and repeated environment initialization must not invalidate
# every dependency. Tool/compiler hashes remain part of the package ABI.
set(VCPKG_ENV_PASSTHROUGH_UNTRACKED PATH)
set(VCPKG_CMAKE_SYSTEM_NAME MinGW)

if(PORT STREQUAL "openblas")
    # Common code must run before runtime dispatch, including on non-AVX CPUs.
    # The manifest enables dynamic-arch for the optimized per-CPU kernels.
    list(APPEND VCPKG_CMAKE_CONFIGURE_OPTIONS "-DTARGET=CORE2")
endif()
