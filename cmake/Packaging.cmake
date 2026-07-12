include_guard(GLOBAL)

install(TARGETS EMTGv9
    RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
    COMPONENT Runtime)

if(BUILD_PROPULATOR)
    install(TARGETS PropulatorDriver propulator
        RUNTIME DESTINATION "${CMAKE_INSTALL_BINDIR}"
        LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
        COMPONENT Runtime)
endif()

if(BUILD_PYHARDWARE)
    install(TARGETS PyHardware
        LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}/emtg"
        RUNTIME DESTINATION "${CMAKE_INSTALL_LIBDIR}/emtg"
        COMPONENT Runtime)
endif()

if(WIN32 AND EMTG_IPOPT_RUNTIME_DLLS)
    install(FILES ${EMTG_IPOPT_RUNTIME_DLLS}
        DESTINATION "${CMAKE_INSTALL_BINDIR}"
        COMPONENT Runtime)
endif()

set(EMTG_HARDWARE_DATA
    AEPS.ThrottleTable
    AEPSx2.ThrottleTable
    BIT3.ThrottleTable
    BIT3x2.ThrottleTable
    HALO12.xlsx
    NEXT-C.xlsx
    PPS5000.ThrottleTable
    PPS5000x2.ThrottleTable
    default.emtg_launchvehicleopt
    default.emtg_powersystemsopt
    default.emtg_propulsionsystemopt
    default.emtg_spacecraftopt
    empty.ThrottleTable
    launch_vehicle_curves.xlsx
    thruster_data.readme)
list(TRANSFORM EMTG_HARDWARE_DATA PREPEND "${PROJECT_SOURCE_DIR}/HardwareModels/")
install(FILES ${EMTG_HARDWARE_DATA}
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/HardwareModels"
    COMPONENT Runtime)

set(EMTG_UNIVERSE_DATA
    Earth.emtg_universe
    Jupiter.emtg_universe
    Mars.emtg_universe
    Saturn.emtg_universe
    Sun.emtg_universe
    Sun_A20136163.emtg_universe
    Sun_RTG.emtg_universe
    Sun_barycenters.emtg_universe
    universe_menu.emtg_menu)
list(TRANSFORM EMTG_UNIVERSE_DATA PREPEND "${PROJECT_SOURCE_DIR}/Universe/")
install(FILES ${EMTG_UNIVERSE_DATA}
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/Universe"
    COMPONENT Runtime)
install(FILES
    "${PROJECT_SOURCE_DIR}/Universe/ephemeris_files/go_get_these_files.txt"
    "${PROJECT_SOURCE_DIR}/Universe/ephemeris_files/naif0012.tls"
    "${PROJECT_SOURCE_DIR}/Universe/ephemeris_files/pck00010.tpc"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/Universe/ephemeris_files"
    COMPONENT Runtime)
install(FILES "${PROJECT_SOURCE_DIR}/PyEMTG/default.emtgopt"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/examples"
    COMPONENT Runtime)
install(FILES
    "${PROJECT_SOURCE_DIR}/README.opensource"
    "${PROJECT_SOURCE_DIR}/EMTG_NOSA_License.pdf"
    "${PROJECT_SOURCE_DIR}/THIRD_PARTY_NOTICES.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/licenses"
    COMPONENT Runtime)
install(FILES "${PROJECT_SOURCE_DIR}/VERSION"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg"
    COMPONENT Runtime)

if(DEFINED VCPKG_INSTALLED_DIR AND VCPKG_TARGET_TRIPLET)
    set(EMTG_VCPKG_SHARE
        "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/share")
    foreach(THIRD_PARTY_PORT
            boost-headers cspice coin-or-ipopt coin-or-mumps
            lapack-reference openblas)
        if(EXISTS "${EMTG_VCPKG_SHARE}/${THIRD_PARTY_PORT}/copyright")
            install(FILES "${EMTG_VCPKG_SHARE}/${THIRD_PARTY_PORT}/copyright"
                DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/licenses/third-party"
                RENAME "${THIRD_PARTY_PORT}.txt"
                COMPONENT Runtime)
        endif()
    endforeach()
endif()

if(MINGW AND EMTG_DEPENDENCY_PROVIDER STREQUAL "managed")
    get_filename_component(EMTG_MINGW_BIN_DIR "${CMAKE_CXX_COMPILER}" DIRECTORY)
    get_filename_component(EMTG_MINGW_PREFIX "${EMTG_MINGW_BIN_DIR}" DIRECTORY)
    set(EMTG_MINGW_LICENSE_ROOT "${EMTG_MINGW_PREFIX}/share/licenses")
    set(EMTG_MINGW_RUNTIME_LICENSES
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING3"
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING.RUNTIME"
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING.LIB"
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/README"
        "${EMTG_MINGW_LICENSE_ROOT}/crt/COPYING.MinGW-w64-runtime.txt"
        "${EMTG_MINGW_LICENSE_ROOT}/crt/COPYING.MinGW-w64.txt"
        "${EMTG_MINGW_LICENSE_ROOT}/libwinpthread/COPYING")
    foreach(EMTG_RUNTIME_LICENSE IN LISTS EMTG_MINGW_RUNTIME_LICENSES)
        if(NOT EXISTS "${EMTG_RUNTIME_LICENSE}")
            message(FATAL_ERROR
                "Managed MinGW runtime notice is missing: ${EMTG_RUNTIME_LICENSE}")
        endif()
    endforeach()
    install(FILES
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING3"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/licenses/compiler-runtime"
        RENAME "gcc-gpl-3.0.txt"
        COMPONENT Runtime)
    install(FILES
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING.RUNTIME"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/licenses/compiler-runtime"
        RENAME "gcc-runtime-library-exception-3.1.txt"
        COMPONENT Runtime)
    install(FILES
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/COPYING.LIB"
        "${EMTG_MINGW_LICENSE_ROOT}/gcc-libs/README"
        "${EMTG_MINGW_LICENSE_ROOT}/crt/COPYING.MinGW-w64-runtime.txt"
        "${EMTG_MINGW_LICENSE_ROOT}/crt/COPYING.MinGW-w64.txt"
        "${EMTG_MINGW_LICENSE_ROOT}/libwinpthread/COPYING"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg/licenses/compiler-runtime"
        COMPONENT Runtime)
endif()

if(NOT WIN32)
    install(CODE
        "file(CREATE_LINK \"EMTGv9\" \"\$ENV{DESTDIR}\${CMAKE_INSTALL_PREFIX}/${CMAKE_INSTALL_BINDIR}/emtg\" SYMBOLIC)"
        COMPONENT Runtime)
    install(FILES "${PROJECT_SOURCE_DIR}/packaging/linux/EXPERIMENTAL.md"
        DESTINATION "${CMAKE_INSTALL_DATADIR}/emtg"
        RENAME "LINUX-EXPERIMENTAL.md"
        COMPONENT Runtime)
endif()

set(CPACK_PACKAGE_NAME "EMTG")
set(CPACK_PACKAGE_VENDOR "Keystone Intelligence")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Evolutionary Mission Trajectory Generator")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_CONTACT "GSFC-DL-TrajOpt-Support@mail.nasa.gov")
set(CPACK_PACKAGE_CHECKSUM SHA256)
set(CPACK_PACKAGE_FILE_NAME "EMTG-${PROJECT_VERSION}-${CMAKE_SYSTEM_NAME}-${CMAKE_SYSTEM_PROCESSOR}")
set(CPACK_RESOURCE_FILE_LICENSE "${PROJECT_SOURCE_DIR}/README.opensource")
set(CPACK_MONOLITHIC_INSTALL ON)

if(WIN32)
    set(CPACK_GENERATOR ZIP)
else()
    set(CPACK_GENERATOR TGZ)
    set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "EXPERIMENTAL Linux build of the Evolutionary Mission Trajectory Generator")
    set(CPACK_PACKAGE_FILE_NAME "EMTG-${PROJECT_VERSION}-Linux-${CMAKE_SYSTEM_PROCESSOR}-experimental")
endif()

include(CPack)
