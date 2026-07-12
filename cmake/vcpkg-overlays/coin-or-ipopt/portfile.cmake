vcpkg_from_github(
    OUT_SOURCE_PATH SOURCE_PATH
    REPO coin-or/Ipopt
    REF ec43e37a06054246764fb116e50e3e30c9ada089
    SHA512 f5b30e81b4a1a178e9a0e2b51b4832f07441b2c3e9a2aa61a6f07807f94185998e985fcf3c34d96fbfde78f07b69f2e0a0675e1e478a4e668da6da60521e0fd6
    HEAD_REF master
)

file(COPY "${CURRENT_INSTALLED_DIR}/share/coin-or-buildtools/" DESTINATION "${SOURCE_PATH}")
set(ENV{ACLOCAL} "aclocal -I \"${SOURCE_PATH}/BuildTools\"")

if(VCPKG_TARGET_IS_MINGW)
    # The reference LAPACK archive contains Fortran objects; spell out its
    # static runtime closure because the wrapper pkg-config modules omit it.
    set(EMTG_LAPACK_OPTION
        "--with-lapack-lflags=-llapack -lopenblas -lgfortran -lquadmath")
else()
    set(EMTG_LAPACK_OPTION --with-lapack)
endif()

vcpkg_configure_make(
    SOURCE_PATH "${SOURCE_PATH}"
    AUTOCONFIG
    OPTIONS
        --without-spral
        --without-hsl
        --without-asl
        "${EMTG_LAPACK_OPTION}"
        --with-mumps
        --enable-relocatable
        --disable-f77
        --disable-java
)

vcpkg_install_make()
vcpkg_copy_pdbs()
vcpkg_fixup_pkgconfig()
file(REMOVE_RECURSE "${CURRENT_PACKAGES_DIR}/debug/include" "${CURRENT_PACKAGES_DIR}/debug/share")
vcpkg_install_copyright(FILE_LIST "${SOURCE_PATH}/LICENSE")
