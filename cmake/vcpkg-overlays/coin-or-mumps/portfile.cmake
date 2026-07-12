vcpkg_download_distfile(HARNESS_ARCHIVE
    URLS "https://github.com/coin-or-tools/ThirdParty-Mumps/archive/63714c7df4444de65d8140ebeca3952a8538dee7.tar.gz"
    FILENAME "ThirdParty-Mumps-3.0.12.tar.gz"
    SHA512 a706ce9e9773e8ad6b71e4f3470eb910d8367eba25ee96f70c628854e9ff24bdec9814d311fcfeebc22e26468cabe3680a6bfed9debadcc989dddf4e3780be4a)
vcpkg_download_distfile(MUMPS_ARCHIVE
    URLS "https://coin-or-tools.github.io/ThirdParty-Mumps/MUMPS_5.8.2.tar.gz"
    FILENAME "MUMPS_5.8.2.tar.gz"
    SHA512 1dcb609194400ad85f403fd09d9ee2ff5979161232b36e075ae0fac4eabc651a9277721a3b7672e3e3b5f2020aad13b920376f1e93bf403891f75c0c385b061a)

vcpkg_extract_source_archive(HARNESS_SOURCE ARCHIVE "${HARNESS_ARCHIVE}")
vcpkg_extract_source_archive(MUMPS_SOURCE ARCHIVE "${MUMPS_ARCHIVE}")
file(RENAME "${MUMPS_SOURCE}" "${HARNESS_SOURCE}/MUMPS")
vcpkg_apply_patches(SOURCE_PATH "${HARNESS_SOURCE}/MUMPS"
    PATCHES "${HARNESS_SOURCE}/mumps_mpi.patch")
file(RENAME "${HARNESS_SOURCE}/MUMPS/libseq/mpi.h"
            "${HARNESS_SOURCE}/MUMPS/libseq/mumps_mpi.h")

file(COPY "${CURRENT_INSTALLED_DIR}/share/coin-or-buildtools/" DESTINATION "${HARNESS_SOURCE}")
set(ENV{ACLOCAL} "aclocal -I \"${HARNESS_SOURCE}/BuildTools\"")
find_program(EMTG_GFORTRAN NAMES gfortran REQUIRED)
set(ENV{FC} "${EMTG_GFORTRAN}")

if(VCPKG_TARGET_IS_MINGW)
    set(EMTG_LAPACK_OPTION
        "--with-lapack-lflags=-llapack -lopenblas -lgfortran -lquadmath")
else()
    set(EMTG_LAPACK_OPTION --with-lapack)
endif()

vcpkg_configure_make(
    SOURCE_PATH "${HARNESS_SOURCE}"
    AUTOCONFIG
    OPTIONS
        "${EMTG_LAPACK_OPTION}"
        --without-metis
        --disable-openmp
)
vcpkg_install_make()
vcpkg_fixup_pkgconfig()
foreach(PC_FILE
        "${CURRENT_PACKAGES_DIR}/lib/pkgconfig/coinmumps.pc"
        "${CURRENT_PACKAGES_DIR}/debug/lib/pkgconfig/coinmumps.pc")
    if(EXISTS "${PC_FILE}")
        file(READ "${PC_FILE}" PC_CONTENT)
        # Compiler search paths leak the build machine into the otherwise
        # relocatable static metadata and are unnecessary for MinGW runtimes.
        string(REGEX REPLACE " \"?-L[^ \"\r\n]+\"?" "" PC_CONTENT "${PC_CONTENT}")
        file(WRITE "${PC_FILE}" "${PC_CONTENT}")
    endif()
endforeach()
file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include" "${CURRENT_PACKAGES_DIR}/debug/share")
vcpkg_install_copyright(FILE_LIST
    "${HARNESS_SOURCE}/LICENSE"
    "${HARNESS_SOURCE}/MUMPS/LICENSE")
