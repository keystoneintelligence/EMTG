cmake_minimum_required(VERSION 3.25)
if(POLICY CMP0167)
    cmake_policy(SET CMP0167 NEW)
endif()

file(STRINGS "${CMAKE_CURRENT_SOURCE_DIR}/VERSION" EMTG_PROJECT_VERSION LIMIT_COUNT 1)
string(STRIP "${EMTG_PROJECT_VERSION}" EMTG_PROJECT_VERSION)
if(NOT EMTG_PROJECT_VERSION MATCHES "^[0-9]+\\.[0-9]+\\.[0-9]+$")
    message(FATAL_ERROR "VERSION must contain a semantic version such as 9.2.0")
endif()

function(emtg_configure_executable target_name)
    if(MINGW AND (EMTG_DEPENDENCY_PROVIDER STREQUAL "managed" OR EMTG_PORTABLE_TESTBED_ONLY))
        target_link_options(${target_name} PRIVATE -static -static-libgcc -static-libstdc++
            $<$<CONFIG:Release>:-s>)
    elseif(UNIX AND NOT APPLE AND EMTG_DEPENDENCY_PROVIDER STREQUAL "managed")
        target_link_options(${target_name} PRIVATE -static-libgcc -static-libstdc++)
    endif()
endfunction()

option(EMTG_PORTABLE_TESTBED_ONLY "Configure only portable testbed targets that do not require CSPICE, SNOPT, IPOPT, or the full EMTG executable." OFF)

if(EMTG_PORTABLE_TESTBED_ONLY)
    project(EMTG VERSION ${EMTG_PROJECT_VERSION} LANGUAGES CXX)
    set(CMAKE_CXX_STANDARD 17)
    set(CMAKE_CXX_STANDARD_REQUIRED ON)
    include(CTest)

    set(BUILD_EMTG_TESTBED ON CACHE BOOL "Build the EMTG testing framework" FORCE)
    set(RUN_ACCELERATION_MODEL_TESTBED OFF CACHE BOOL "Do you want to run the acceleration model testbed?" FORCE)
    set(RUN_MISSION_TESTBED OFF CACHE BOOL "Do you want to run the mission testbed?" FORCE)
    set(RUN_MISSIONOPTIONS_TESTBED ON CACHE BOOL "Do you want to run the missionoptions testbed?" FORCE)
    set(RUN_ADOUBLE_TESTBED ON CACHE BOOL "Do you want to run the local adouble compatibility tests?" FORCE)
    set(RUN_INTEGRATION_TESTBED ON CACHE BOOL "Do you want to run the local integration tests?" FORCE)
    set(RUN_EPHEMERIS_DERIVATIVE_TESTBED ON CACHE BOOL "Do you want to run the ephemeris derivative quality tests?" FORCE)
    set(USE_AD_INSTRUMENTATION OFF CACHE BOOL "Do you want to perform derivative accuracy checks with AD?" FORCE)
    set(SPLINE_EPHEM OFF CACHE BOOL "Enable SplineEphem" FORCE)

    if(EXISTS "${CMAKE_SOURCE_DIR}/depend/boost/boost")
        set(BOOST_ROOT "${CMAKE_SOURCE_DIR}/depend/boost" CACHE PATH "Boost root directory")
        set(BOOST_INCLUDE_DIR "${BOOST_ROOT}" CACHE PATH "Boost include directory")
    endif()

    if(BOOST_INCLUDE_DIR)
        set(Boost_FOUND TRUE)
        set(Boost_INCLUDE_DIRS "${BOOST_INCLUDE_DIR}")
        message(STATUS "Using vendored Boost headers from ${BOOST_INCLUDE_DIR}")
    else()
        find_package(Boost 1.60 REQUIRED)
    endif()

    include_directories(${Boost_INCLUDE_DIRS})
    include_directories(src/Core src/Math src/Utilities)

    add_subdirectory(src/Testing)
    return()
endif()

# Pull in optional machine-local hints. All dependencies can also be supplied
# through ordinary CMake cache variables, package discovery, or a toolchain.
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/cmake" "${CMAKE_SOURCE_DIR}")
if(NOT EMTG_DEPENDENCY_PROVIDER STREQUAL "managed")
    include(EMTG-Config.cmake OPTIONAL)
else()
    # A release build must never inherit paths from a prior developer cache or
    # an ignored machine-local EMTG-Config.cmake.
    foreach(machine_hint
            BOOST_INCLUDE_DIR BOOST_LIBRARY_DIRS BOOST_ROOT
            CSPICE_DIR CSPICE_ROOT_DIR CSPICE_INCLUDE_DIR CSPICE_LIBRARY_FILE
            IPOPT_ROOT_DIR IPOPTDIR_OVRD IPOPT_INCLUDE_DIR IPOPT_LIBRARY IPOPT_LIBRARY_DIR
            IPOPT_RUNTIME_LIBRARY IPOPT_BIN_DIR IPOPT_MINGW_BIN_DIR
            IPOPT_C_INCLUDE_DIR IPOPT_DISCOVERED_LIBRARY IPOPT_DISCOVERED_RUNTIME_LIBRARY)
        unset(${machine_hint} CACHE)
        unset(${machine_hint})
    endforeach()
endif()

#identify the project
project(EMTG VERSION ${EMTG_PROJECT_VERSION} LANGUAGES C CXX)

set(CMAKE_C_STANDARD 17)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(CTest)
include(GNUInstallDirs)

set(CSPICE_DIR "${CSPICE_DIR}" CACHE PATH "CSPICE source/build root")
if(NOT CSPICE_DIR AND NOT EMTG_DEPENDENCY_PROVIDER STREQUAL "managed")
    set(CSPICE_DIR "${PROJECT_SOURCE_DIR}/depend/cspice" CACHE PATH "CSPICE source/build root" FORCE)
endif()

# enable multicore build on Windows
if (MSVC)
    string(APPEND CMAKE_CXX_FLAGS " /MP")
    string(APPEND CMAKE_C_FLAGS " /MP")
endif()

# Boost spews auto_ptr deprecation warnings
add_definitions(-D_HAS_AUTO_PTR_ETC)
if (NOT MSVC)
    string(APPEND CMAKE_C_FLAGS " -Wno-deprecated-declarations")
    string(APPEND CMAKE_CXX_FLAGS " -Wno-deprecated-declarations")
endif()

# Boost doesn't recognize newer compilers
add_definitions(-DBOOST_CONFIG_SUPPRESS_OUTDATED_MESSAGE)

# Boost doesn't like std::iterator either
add_definitions(-D_SILENCE_CXX17_ITERATOR_BASE_CLASS_DEPRECATION_WARNING)

# Build artifacts stay in the build tree. Installation and packaging use the
# standard GNUInstallDirs layout on every platform.
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")
set(CMAKE_ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib")


#compiler-time Define-level options  for EMTG

option(EMTG_ENABLE_SNOPT "Build EMTG with SNOPT support" OFF)
option(EMTG_ENABLE_IPOPT "Build EMTG with IPOPT support" ON)

# Transition aliases retained for existing scripts and cache files.
if(DEFINED ENABLE_SNOPT AND NOT ENABLE_SNOPT STREQUAL EMTG_ENABLE_SNOPT)
    message(DEPRECATION "ENABLE_SNOPT is deprecated; use EMTG_ENABLE_SNOPT")
    set(EMTG_ENABLE_SNOPT "${ENABLE_SNOPT}" CACHE BOOL "Build EMTG with SNOPT support" FORCE)
endif()
if(DEFINED ENABLE_IPOPT AND NOT ENABLE_IPOPT STREQUAL EMTG_ENABLE_IPOPT)
    message(DEPRECATION "ENABLE_IPOPT is deprecated; use EMTG_ENABLE_IPOPT")
    set(EMTG_ENABLE_IPOPT "${ENABLE_IPOPT}" CACHE BOOL "Build EMTG with IPOPT support" FORCE)
endif()
set(ENABLE_SNOPT "${EMTG_ENABLE_SNOPT}")
set(ENABLE_IPOPT "${EMTG_ENABLE_IPOPT}")

# Optional user override of the snopt directory specified in the EMTG-Config.cmake file
set(SNOPTDIR_OVRD "${SNOPT_ROOT_DIR}" CACHE PATH "SNOPT Root Directory") # Do not use the FORCE command here so the value the user enters in GUI is preserved
set(IPOPTDIR_OVRD "${IPOPT_ROOT_DIR}" CACHE PATH "Legacy alias for IPOPT_ROOT_DIR")

option(QUIET_SNOPT "Minimizes the SNOPT output to the log." OFF)
option(SAFE_SNOPT "Trap things that could blow up SNOPT" ON)
option(SNOPT_MINGW_DLL "Use SNOPT built with MINGW in Windows" ON)
option(IPOPT_MINGW_DLL "Use IPOPT built with MINGW in Windows" ON)
option(DEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION "Check decision and objective/constraint vectors for NaN and inf" OFF)
option(FAST_EMTG_MATRIX "Enable fast EMTG matrix class (removes bounds checking)" ON)
option(EMTG_ENABLE_SPLINE_EPHEM "Enable SplineEphem" ON)
option(EMTG_BUILD_TESTS "Build the EMTG testing framework" OFF)
option(EMTG_BUILD_PROPULATOR "Build the Propulator" OFF)
option(EMTG_BUILD_PYHARDWARE "Build PyHardware" OFF)
set(EMTG_DEPENDENCY_PROVIDER "system" CACHE STRING "Dependency provider: system or managed")
set_property(CACHE EMTG_DEPENDENCY_PROVIDER PROPERTY STRINGS system managed)

foreach(legacy_pair
        "SPLINE_EPHEM;EMTG_ENABLE_SPLINE_EPHEM"
        "BUILD_EMTG_TESTBED;EMTG_BUILD_TESTS"
        "BUILD_PROPULATOR;EMTG_BUILD_PROPULATOR"
        "BUILD_PYHARDWARE;EMTG_BUILD_PYHARDWARE")
    list(GET legacy_pair 0 legacy_name)
    list(GET legacy_pair 1 modern_name)
    if(DEFINED ${legacy_name} AND NOT "${${legacy_name}}" STREQUAL "${${modern_name}}")
        message(DEPRECATION "${legacy_name} is deprecated; use ${modern_name}")
        set(${modern_name} "${${legacy_name}}" CACHE BOOL "EMTG compatibility option" FORCE)
    endif()
endforeach()
set(SPLINE_EPHEM "${EMTG_ENABLE_SPLINE_EPHEM}")
set(BUILD_EMTG_TESTBED "${EMTG_BUILD_TESTS}")
set(BUILD_PROPULATOR "${EMTG_BUILD_PROPULATOR}")
set(BUILD_PYHARDWARE "${EMTG_BUILD_PYHARDWARE}")

# We want Background mode to default to ON for NavLab runs
if (MSVC)
    option(BACKGROUND_MODE "This suppresses 'press any key to continue' at the end of an EMTG run" OFF)
else()
    option(BACKGROUND_MODE "This suppresses 'press any key to continue' at the end of an EMTG run" ON)
endif()

#switches for files that open source users don't have access to
option(HAS_PROBEENTRYPHASE "Build with ProbeEntryPhase?" ON)
option(HAS_BUILT_IN_THRUSTERS "Build with built-in thruster library?" ON)
mark_as_advanced(FORCE HAS_PROBEENTRYPHASE HAS_BUILT_IN_THRUSTERS BUILD_EMTG_TESTBED BUILD_PROPULATOR BUILD_PYHARDWARE SPLINE_EPHEM DEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION FAST_EMTG_MATRIX)

set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build in Debug or Release mode")
set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS Debug Release)

if(ENABLE_SNOPT)
    add_definitions(-DEMTG_ENABLE_SNOPT)
endif()

if(ENABLE_IPOPT)
    add_definitions(-DEMTG_ENABLE_IPOPT)
endif()

set(EMTG_CAPABILITY_SNOPT false)
set(EMTG_CAPABILITY_IPOPT false)
if(ENABLE_SNOPT)
    set(EMTG_CAPABILITY_SNOPT true)
endif()
if(ENABLE_IPOPT)
    set(EMTG_CAPABILITY_IPOPT true)
endif()
configure_file(
    "${CMAKE_SOURCE_DIR}/cmake/solver_capabilities.json.in"
    "${CMAKE_BINARY_DIR}/solver_capabilities.json"
    @ONLY)

set(EMTG_NLP_BACKENDS)
if(ENABLE_SNOPT)
    list(APPEND EMTG_NLP_BACKENDS SNOPT)
endif()
if(ENABLE_IPOPT)
    list(APPEND EMTG_NLP_BACKENDS IPOPT)
endif()
if(NOT EMTG_NLP_BACKENDS)
    message(FATAL_ERROR "No NLP solver backend is enabled. Enable IPOPT, SNOPT, or both.")
endif()
message(STATUS "Enabled EMTG NLP backends: ${EMTG_NLP_BACKENDS}")

message(STATUS "Creating build files for " ${CMAKE_BUILD_TYPE} " mode... \n")

if(CMAKE_BUILD_TYPE MATCHES Release)
    if (${CMAKE_CXX_COMPILER_ID} MATCHES "MSVC")
        string(APPEND CMAKE_CXX_FLAGS_RELEASE " /Ot /Ox /GL")
    elseif (${CMAKE_CXX_COMPILER_ID} MATCHES "GNU")
        set(CMAKE_CXX_FLAGS_RELEASE " -O2 -DNDEBUG")
    endif()
	message(STATUS "The following compiler optimization flags have been set: " ${CMAKE_CXX_FLAGS_RELEASE} "\n")
elseif(CMAKE_BUILD_TYPE MATCHES Debug)
	message(STATUS "The following compiler optimization flags have been set: " ${CMAKE_CXX_FLAGS_DEBUG} "\n")
endif()

if(QUIET_SNOPT)
	add_definitions(-DQUIET_SNOPT)
endif(QUIET_SNOPT)

if(BACKGROUND_MODE)
    message(STATUS "Background mode activated")
	add_definitions(-DBACKGROUND_MODE)
endif(BACKGROUND_MODE)

if(SAFE_SNOPT)
	add_definitions(-DSAFE_SNOPT)
endif(SAFE_SNOPT)

if(DEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION)
	add_definitions(-DDEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION)
endif(DEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION)

if(FAST_EMTG_MATRIX)
    add_definitions(-DFAST_EMTG_MATRIX)
endif(FAST_EMTG_MATRIX)


if(HAS_PROBEENTRYPHASE)
    add_definitions(-DHAS_PROBEENTRYPHASE)
endif(HAS_PROBEENTRYPHASE)

if(HAS_BUILT_IN_THRUSTERS)
    add_definitions(-DHAS_BUILT_IN_THRUSTERS)
endif(HAS_BUILT_IN_THRUSTERS)

if(SPLINE_EPHEM)
    add_definitions(-DSPLINE_EPHEM)
endif()

#----------------CSpice Items------------------------------------------------------
message(STATUS \n "Now checking for CSpice...")
find_path(CSPICE_INCLUDE_DIR
    NAMES SpiceUsr.h
    HINTS "${CSPICE_DIR}" "${CSPICE_ROOT_DIR}" "$ENV{CSPICE_ROOT}"
          "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}"
    PATH_SUFFIXES include include/cspice)
find_library(CSPICE_LIBRARY_FILE
    NAMES cspice libcspice
    HINTS "${CSPICE_DIR}" "${CSPICE_ROOT_DIR}" "$ENV{CSPICE_ROOT}"
          "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}"
    PATH_SUFFIXES lib lib64)
if(NOT CSPICE_LIBRARY_FILE)
    find_file(CSPICE_LIBRARY_FILE
        NAMES cspice.a libcspice.a cspice.lib
        HINTS "${CSPICE_DIR}" "${CSPICE_ROOT_DIR}" "$ENV{CSPICE_ROOT}"
              "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}"
        PATH_SUFFIXES lib lib64)
endif()

if(NOT CSPICE_INCLUDE_DIR OR NOT CSPICE_LIBRARY_FILE)
    message(FATAL_ERROR
        "Could not find CSPICE headers and library. Set CSPICE_DIR/CSPICE_ROOT, "
        "or use the managed dependency preset.")
endif()

add_library(EMTG_cspice STATIC IMPORTED)
set_property(TARGET EMTG_cspice PROPERTY IMPORTED_LOCATION "${CSPICE_LIBRARY_FILE}")
set_property(TARGET EMTG_cspice PROPERTY INTERFACE_INCLUDE_DIRECTORIES "${CSPICE_INCLUDE_DIR}")
set(CSPICE_LIBRARY EMTG_cspice)
set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ${CSPICE_LIBRARY})
message(STATUS "CSPICE library: ${CSPICE_LIBRARY_FILE}")

#----------------------------SNOPT Items-------------------------------------
if(ENABLE_SNOPT)
message(STATUS \n "Now checking on SNOPT...")

if (NOT (IS_DIRECTORY ${SNOPTDIR_OVRD}))
	# Throw an error if the directory specified does not exist
	message(FATAL_ERROR "SNOPT directory specified (${SNOPTDIR_OVRD}) does not exist")
endif()

# Check for the latest versions of SNOPT first
if (IS_DIRECTORY ${SNOPTDIR_OVRD}/include OR IS_DIRECTORY ${SNOPTDIR_OVRD}/interfaces)

	# Check for specific SNOPT version starting with the most used version
	if (IS_DIRECTORY ${SNOPTDIR_OVRD}/interfaces AND NOT IS_DIRECTORY ${SNOPTDIR_OVRD}/cwrap)
		# SNOPT 7.6
		message(STATUS "SNOPT 7.6 found")
		
		add_definitions(-DSNOPT76)
		set(SNOPT_75_PLUS_DIR ${SNOPTDIR_OVRD}/interfaces)
		
		message(STATUS "Directory containing SNOPT interface files: " ${SNOPT_75_PLUS_DIR}/include)
		
		include_directories("${SNOPT_75_PLUS_DIR}/include")
	
		set(SNOPT_INTERFACE_HEADERS ${SNOPT_75_PLUS_DIR}/include/snoptProblem.hpp ${SNOPT_75_PLUS_DIR}/include/snopt.h)
		set(SNOPT_INTERFACE_SOURCE ${SNOPT_75_PLUS_DIR}/src/snoptProblem.cpp)
	elseif 	(IS_DIRECTORY ${SNOPTDIR_OVRD}/include AND IS_DIRECTORY ${SNOPTDIR_OVRD}/ampl/solvers/snopt)
		# SNOPT 7.7
		message(STATUS "SNOPT 7.7 found")
		
		add_definitions(-DSNOPT76)
		set(SNOPT_75_PLUS_DIR ${SNOPTDIR_OVRD})
		
		message(STATUS "Directory containing SNOPT interface files: " ${SNOPT_75_PLUS_DIR}/include)
		
		include_directories("${SNOPT_75_PLUS_DIR}/include")
	
		set(SNOPT_INTERFACE_HEADERS ${SNOPT_75_PLUS_DIR}/include/snoptProblem.hpp ${SNOPT_75_PLUS_DIR}/include/snopt.h)
		set(SNOPT_INTERFACE_SOURCE ${SNOPT_75_PLUS_DIR}/src/snoptProblem.cpp)
	
		
	elseif (IS_DIRECTORY ${SNOPTDIR_OVRD}/cwrap)
		# SNOPT 7.5
		
		message(STATUS "SNOPT 7.5 found")
		
		add_definitions(-DSNOPT75)
		set(SNOPT_75_PLUS_DIR ${SNOPTDIR_OVRD}/interfaces)
		
		message(STATUS "Directory containing SNOPT interface files: " ${SNOPT_75_PLUS_DIR}/include)
		
		include_directories("${SNOPT_75_PLUS_DIR}/include")
	
		set(SNOPT_INTERFACE_HEADERS ${SNOPT_75_PLUS_DIR}/include/snoptProblem.hpp ${SNOPT_75_PLUS_DIR}/include/snopt.h)
		set(SNOPT_INTERFACE_SOURCE ${SNOPT_75_PLUS_DIR}/src/snoptProblem.cpp)
	endif()

    
    if (MSVC AND SNOPT_MINGW_DLL)
    
        set(SNOPT_LIBRARIES ${SNOPTDIR_OVRD}/build/${CMAKE_CFG_INTDIR}/libsnopt.lib)
        
        add_library(snopt_interface STATIC IMPORTED)
        set_property(TARGET snopt_interface PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/build/${CMAKE_CFG_INTDIR}/snopt_interface.lib")
        link_directories(${SNOPTDIR_OVRD}/build/${CMAKE_CFG_INTDIR})
        set(SNOPT_LIBRARIES ${SNOPT_LIBRARIES} snopt_interface)
    
    else()
	
        #PULL IN SNOPT.  THIS ASSUMES SNOPT HAS ALREADY BEEN COMPILED AND IS IN A VERY SPECIFIC SPOT.
        if (NOT MSVC)
            add_library(snopt SHARED IMPORTED)
            add_library(snopt_cpp SHARED IMPORTED)
            set_property(TARGET snopt PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/.libs/libsnopt7.so")
            set_property(TARGET snopt_cpp PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/.libs/libsnopt7_cpp.so")
        else(NOT MSVC)
            add_library(snopt STATIC IMPORTED)
            set_property(TARGET snopt PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/snopt.lib")
            set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ifmodintr.lib)
            link_directories("${INTEL_FORTRAN_DIR}")
            
        endif(NOT MSVC)        

        if (MSVC)
            link_directories("${PROJECT_SOURCE_DIR}/lib")
            link_directories(${SNOPTDIR_OVRD}/lib)
        else()
            link_directories("${PROJECT_SOURCE_DIR}/lib")
            link_directories(${SNOPTDIR_OVRD}/lib/.libs)
        endif()
        add_library(snopt_interface ${SNOPT_INTERFACE_HEADERS} ${SNOPT_INTERFACE_SOURCE})
        if (MSVC)
            set(SNOPT_LIBRARIES snopt_interface snopt)
        else()
            target_link_libraries(snopt_interface gfortran)
            set(SNOPT_LIBRARIES snopt_interface snopt_cpp snopt)
        endif()
        
        set_property(TARGET snopt_interface PROPERTY POSITION_INDEPENDENT_CODE ON)
        
    endif()

elseif(IS_DIRECTORY ${SNOPTDIR_OVRD}/cppsrc)
# SNOPT 7.2

	message(STATUS "SNOPT 7.2 found")
	
	add_definitions(-DSNOPT72 "Use SNOPT 7.2-12 or older" ON)

	include_directories("${SNOPTDIR_OVRD}/cppsrc")
	include_directories("${SNOPTDIR_OVRD}/win32")
	set(SNOPT_INTERFACE_HEADERS ${SNOPTDIR_OVRD}/win32/f2c.h ${SNOPTDIR_OVRD}/cppsrc/snoptProblem.hh ${SNOPTDIR_OVRD}/cppsrc/snopt.hh ${SNOPTDIR_OVRD}/cppsrc/snfilewrapper.hh)
	set(SNOPT_INTERFACE_SOURCE ${SNOPTDIR_OVRD}/cppsrc/snfilewrapper.c ${SNOPTDIR_OVRD}/cppsrc/snoptProblem.cc)
	
	#PULL IN SNOPT.  THIS ASSUMES SNOPT HAS ALREADY BEEN COMPILED AND IS IN A VERY SPECIFIC SPOT.
	add_library(f2c STATIC IMPORTED)
	add_library(snopt_cpp STATIC IMPORTED)
	add_library(snopt_c STATIC IMPORTED)
	add_library(snoptsnprint STATIC IMPORTED)
	
	if (NOT MSVC)
		set_property(TARGET f2c PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/libf2c.a")
		set_property(TARGET snopt_cpp PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/libsnopt_cpp.a")
		set_property(TARGET snoptsnprint PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/libsnprint_c.a")
		set_property(TARGET snopt_c PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/lib/libsnopt_c.a")
		set(SNOPT_LIBRARIES snopt_c snoptsnprint snopt_cpp f2c)
	else(NOT MSVC)
		set_property(TARGET f2c PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/win32/snopt_csrc/Release/vcf2c.lib")
		set_property(TARGET snopt_c PROPERTY IMPORTED_LOCATION "${SNOPTDIR_OVRD}/win32/snopt_csrc/Release/snopt.lib")
		set(SNOPT_LIBRARIES snopt_c f2c)
	endif(NOT MSVC)
	
	link_directories("${PROJECT_SOURCE_DIR}/lib")

else(IS_DIRECTORY ${SNOPTDIR_OVRD}/interfaces)
	message(FATAL_ERROR "Could not find snopt")
endif()

set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ${SNOPT_LIBRARIES})
else()
message(STATUS "SNOPT support disabled")
endif()

#----------------------------IPOPT Items-------------------------------------
if(ENABLE_IPOPT)
message(STATUS \n "Now checking on IPOPT...")

find_package(Ipopt 3.14 REQUIRED)
find_path(EMTG_IPOPT_C_INCLUDE_DIR
    NAMES IpStdCInterface.h
    HINTS "${IPOPT_INCLUDE_DIR}" "${IPOPT_ROOT_DIR}" "$ENV{IPOPT_ROOT_DIR}"
    PATH_SUFFIXES coin-or coin include/coin-or include/coin)
if(NOT EMTG_IPOPT_C_INCLUDE_DIR)
    message(FATAL_ERROR "Ipopt was found, but IpStdCInterface.h is not reachable from its include path")
endif()
set_property(TARGET Ipopt::Ipopt APPEND PROPERTY
    INTERFACE_INCLUDE_DIRECTORIES "${EMTG_IPOPT_C_INCLUDE_DIR}")
set(IPOPT_INCLUDE_DIR "${EMTG_IPOPT_C_INCLUDE_DIR}")
if(EMTG_DEPENDENCY_PROVIDER STREQUAL "managed" AND MSVC)
    # The stock MSVC vcpkg Ipopt package does not propagate its BLAS/LAPACK
    # link interface. MinGW and Linux receive these flags from ipopt.pc.
    find_library(EMTG_LAPACK_LIBRARY NAMES lapack
        HINTS "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/lib"
        REQUIRED NO_DEFAULT_PATH)
    find_library(EMTG_BLAS_LIBRARY NAMES blas
        HINTS "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/lib"
        REQUIRED NO_DEFAULT_PATH)
    set_property(TARGET Ipopt::Ipopt APPEND PROPERTY
        INTERFACE_LINK_LIBRARIES "${EMTG_LAPACK_LIBRARY};${EMTG_BLAS_LIBRARY}")
elseif(EMTG_DEPENDENCY_PROVIDER STREQUAL "managed" AND MINGW)
    # CMake's FindPkgConfig is not guaranteed to be available before vcpkg's
    # MSYS tools are on PATH, so preserve the static closure from ipopt.pc here.
    find_library(EMTG_LAPACK_LIBRARY NAMES lapack
        HINTS "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/lib"
        REQUIRED NO_DEFAULT_PATH)
    find_library(EMTG_OPENBLAS_LIBRARY NAMES openblas
        HINTS "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/lib"
        REQUIRED NO_DEFAULT_PATH)
    find_library(EMTG_MUMPS_LIBRARY NAMES coinmumps
        HINTS "${VCPKG_INSTALLED_DIR}/${VCPKG_TARGET_TRIPLET}/lib"
        REQUIRED NO_DEFAULT_PATH)
    set_property(TARGET Ipopt::Ipopt APPEND PROPERTY INTERFACE_LINK_LIBRARIES
        "${EMTG_MUMPS_LIBRARY};${EMTG_LAPACK_LIBRARY};${EMTG_OPENBLAS_LIBRARY};gfortran;quadmath;pthread")
endif()
set(IPOPT_LIBRARIES Ipopt::Ipopt)
set(EMTG_IPOPT_RUNTIME_DLLS ${IPOPT_RUNTIME_DLLS})
set(EMTG_LIBRARIES ${EMTG_LIBRARIES} Ipopt::Ipopt)
message(STATUS "Ipopt ${IPOPT_VERSION} include directory: ${IPOPT_INCLUDE_DIR}")
if(IPOPT_RUNTIME_LIBRARY)
    message(STATUS "Ipopt runtime library: ${IPOPT_RUNTIME_LIBRARY}")
endif()
else()
message(STATUS "IPOPT support disabled")
endif()

#-------------------------Boost Items------------------------------
message(STATUS \n "Now looking for BOOST..")
if(BOOST_INCLUDE_DIR AND EXISTS "${BOOST_INCLUDE_DIR}/boost")
    set(Boost_FOUND TRUE)
    set(Boost_INCLUDE_DIRS "${BOOST_INCLUDE_DIR}")
    message(STATUS "Using Boost headers from ${BOOST_INCLUDE_DIR}")
else()
    find_package(Boost 1.60 REQUIRED)
endif()

include_directories(${Boost_INCLUDE_DIRS})

#pull in EMTG itself
add_subdirectory(src)

include(cmake/Packaging.cmake)


