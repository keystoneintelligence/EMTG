cmake_minimum_required(VERSION 3.8)

option(EMTG_PORTABLE_TESTBED_ONLY "Configure only portable testbed targets that do not require CSPICE, SNOPT, IPOPT, or the full EMTG executable." OFF)

if(EMTG_PORTABLE_TESTBED_ONLY)
    project(EMTG LANGUAGES CXX)
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

    if(EXISTS "${CMAKE_SOURCE_DIR}/depend/boost/stage/lib")
        set(BOOST_ROOT "${CMAKE_SOURCE_DIR}/depend/boost" CACHE PATH "Boost root directory")
        set(BOOST_INCLUDE_DIR "${BOOST_ROOT}" CACHE PATH "Boost include directory")
        set(BOOST_LIBRARY_DIRS "${BOOST_ROOT}/stage/lib" CACHE PATH "Boost library directory")
    endif()

    if(BOOST_INCLUDE_DIR AND BOOST_LIBRARY_DIRS)
        set(EMTG_LOCAL_BOOST_LIBRARIES)
        foreach(BOOST_COMPONENT filesystem system)
            if(MSVC)
                file(GLOB EMTG_BOOST_COMPONENT_LIBRARY "${BOOST_LIBRARY_DIRS}/*boost_${BOOST_COMPONENT}*.lib")
            else()
                file(GLOB EMTG_BOOST_COMPONENT_LIBRARY "${BOOST_LIBRARY_DIRS}/*boost_${BOOST_COMPONENT}*.a" "${BOOST_LIBRARY_DIRS}/*boost_${BOOST_COMPONENT}*.so")
            endif()
            list(LENGTH EMTG_BOOST_COMPONENT_LIBRARY EMTG_BOOST_COMPONENT_LIBRARY_COUNT)
            if(EMTG_BOOST_COMPONENT_LIBRARY_COUNT GREATER 0)
                list(GET EMTG_BOOST_COMPONENT_LIBRARY 0 EMTG_BOOST_COMPONENT_LIBRARY_FIRST)
                list(APPEND EMTG_LOCAL_BOOST_LIBRARIES "${EMTG_BOOST_COMPONENT_LIBRARY_FIRST}")
            else()
                set(EMTG_LOCAL_BOOST_LIBRARIES)
                break()
            endif()
        endforeach()

        if(EMTG_LOCAL_BOOST_LIBRARIES)
            set(Boost_FOUND TRUE)
            set(Boost_INCLUDE_DIRS "${BOOST_INCLUDE_DIR}")
            set(Boost_LIBRARY_DIRS "${BOOST_LIBRARY_DIRS}")
            set(Boost_LIBRARIES ${EMTG_LOCAL_BOOST_LIBRARIES})
            message(STATUS "Using vendored Boost libraries from ${BOOST_LIBRARY_DIRS}")
        endif()
    endif()

    if(NOT Boost_FOUND)
        find_package(Boost 1.60 COMPONENTS filesystem system REQUIRED)
    endif()

    include_directories(${Boost_INCLUDE_DIRS})
    include_directories(src/Core src/Math src/Utilities)
    link_directories(${Boost_LIBRARY_DIRS})

    add_subdirectory(src/Testing)
    return()
endif()

# Pull in optional machine-local hints. All dependencies can also be supplied
# through ordinary CMake cache variables, package discovery, or a toolchain.
list(APPEND CMAKE_MODULE_PATH "${CMAKE_SOURCE_DIR}/cmake" "${CMAKE_SOURCE_DIR}")
include(EMTG-Config.cmake OPTIONAL)

#identify the project
project(EMTG)

include(CTest)

set(CSPICE_DIR "${CSPICE_DIR}" CACHE PATH "CSPICE source/build root")
if(NOT CSPICE_DIR)
    set(CSPICE_DIR "${PROJECT_SOURCE_DIR}/depend/cspice" CACHE PATH "CSPICE source/build root" FORCE)
endif()

link_directories(${PROJECT_SOURCE_DIR}/Release)
link_directories(${PROJECT_SOURCE_DIR}/Debug)


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

# specify where the executable will be installed
if(MSVC)
	STRING(REGEX REPLACE "/" "\\\\" OUTPUTDESTINATION "${PROJECT_SOURCE_DIR}\\bin")
else(MSVC)
	set(OUTPUTDESTINATION ${PROJECT_SOURCE_DIR}/bin)
endif(MSVC)
if(NOT IS_DIRECTORY ${PROJECT_SOURCE_DIR}/bin)
	file(MAKE_DIRECTORY ${OUTPUTDESTINATION})
endif(NOT IS_DIRECTORY ${PROJECT_SOURCE_DIR}/bin)


#compiler-time Define-level options  for EMTG

option(ENABLE_SNOPT "Build EMTG with SNOPT support" OFF)
option(ENABLE_IPOPT "Build EMTG with IPOPT support" ON)

# Optional user override of the snopt directory specified in the EMTG-Config.cmake file
set(SNOPTDIR_OVRD "${SNOPT_ROOT_DIR}" CACHE PATH "SNOPT Root Directory") # Do not use the FORCE command here so the value the user enters in GUI is preserved
set(IPOPTDIR_OVRD "${IPOPT_ROOT_DIR}" CACHE PATH "Legacy alias for IPOPT_ROOT_DIR")

option(QUIET_SNOPT "Minimizes the SNOPT output to the log." OFF)
option(SAFE_SNOPT "Trap things that could blow up SNOPT" ON)
option(SNOPT_MINGW_DLL "Use SNOPT built with MINGW in Windows" ON)
option(IPOPT_MINGW_DLL "Use IPOPT built with MINGW in Windows" ON)
option(DEBUG_CHECK_VARIABLES_AND_CONSTRAINTS_IN_USER_FUNCTION "Check decision and objective/constraint vectors for NaN and inf" OFF)
option(FAST_EMTG_MATRIX "Enable fast EMTG matrix class (removes bounds checking)" ON)
option(SPLINE_EPHEM "Enable SplineEphem" ON)
option(BUILD_EMTG_TESTBED "Build the EMTG testing framework" OFF)
option(BUILD_PROPULATOR "Build the Propulator" OFF)
option(BUILD_PYHARDWARE "Build PyHardware" OFF)

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
        set(CMAKE_CXX_FLAGS_RELEASE " -g -O2")
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

    # Prefer explicit paths for the vendored Windows build, then fall back to
    # CMake's standard GSL package discovery on Linux/macOS.
    find_path(EMTG_GSL_INCLUDE_DIR
        NAMES gsl/gsl_spline.h
        HINTS "${GSL_PATH}" "${GSL_ROOT_DIR}"
        PATH_SUFFIXES include)
    find_library(EMTG_GSL_LIBRARY
        NAMES gsl libgsl
        HINTS "${GSL_PATH}" "${GSL_ROOT_DIR}"
        PATH_SUFFIXES "${CMAKE_BUILD_TYPE}" Release Debug lib lib64)
    find_library(EMTG_GSLCBLAS_LIBRARY
        NAMES gslcblas libgslcblas
        HINTS "${GSL_PATH}" "${GSL_ROOT_DIR}"
        PATH_SUFFIXES "${CMAKE_BUILD_TYPE}" Release Debug lib lib64)

    if(EMTG_GSL_INCLUDE_DIR AND EMTG_GSL_LIBRARY AND EMTG_GSLCBLAS_LIBRARY)
        add_library(EMTG::gsl UNKNOWN IMPORTED)
        add_library(EMTG::gslcblas UNKNOWN IMPORTED)
        set_property(TARGET EMTG::gsl PROPERTY IMPORTED_LOCATION "${EMTG_GSL_LIBRARY}")
        set_property(TARGET EMTG::gsl PROPERTY INTERFACE_INCLUDE_DIRECTORIES "${EMTG_GSL_INCLUDE_DIR}")
        set_property(TARGET EMTG::gslcblas PROPERTY IMPORTED_LOCATION "${EMTG_GSLCBLAS_LIBRARY}")
        set(GSL_INCLUDE_DIRS "${EMTG_GSL_INCLUDE_DIR}")
        set(GSL_LIBRARIES EMTG::gsl EMTG::gslcblas)
    else()
        find_package(GSL REQUIRED)
        set(GSL_LIBRARIES GSL::gsl GSL::gslcblas)
    endif()

    include_directories(${GSL_INCLUDE_DIRS})
    set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ${GSL_LIBRARIES})
endif(SPLINE_EPHEM)

#----------------CSpice Items------------------------------------------------------
message(STATUS \n "Now checking for CSpice...")
if (MSVC) # aka 'windows'
	set(CSPICE_FILE_TO_BE_CORRECTED "${CSPICE_DIR}/src/cspice/mkprodct.bat")
	set(CSPICE_MAKEFILE "makeall.bat")
else (MSVC) #aka Linux or Mac, as both of those use the mkprodct.csh system
	set(CSPICE_FILE_TO_BE_CORRECTED "${CSPICE_DIR}/src/cspice/mkprodct.csh")
	set(CSPICE_MAKEFILE "makeall.csh")
endif(MSVC)


if (EXISTS "${CSPICE_DIR}/${CSPICE_MAKEFILE}")
	message(STATUS "Cspice located and unzipped correctly.")
else()
	message(FATAL_ERROR "CSPICE is not unzipped into the emtg/cspice folder correctly.")
endif()

if (NOT MSVC) #cspice doesn't label the file correctly when it is done; easier to just rename it
	if (NOT EXISTS "${CSPICE_DIR}/lib/libcspice.a")
		message(STATUS "Renaming cspice.a to libcspice.a")
		file(RENAME "${CSPICE_DIR}/lib/cspice.a" "${CSPICE_DIR}/lib/libcspice.a")
		if (NOT EXISTS "${CSPICE_DIR}/lib/libcspice.a")
			message(FATAL_ERROR "CSpice not built properly for a Unix System.")
		endif()
	endif()
else()
	if(NOT EXISTS "${CSPICE_DIR}/lib/cspice.lib")	
		message(FATAL_ERROR "CSpice not built properly for a Windows System.  Please run the cspice/makeall.bat from within the Visual Studio Command Line tools and then reconfigure cmake.")
	endif()	
endif()

set(CSPICE_LIBRARY cspice)
set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ${CSPICE_LIBRARY})
include_directories("${CSPICE_DIR}/include")
link_directories("${CSPICE_DIR}/lib")

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
set(Boost_FOUND FALSE)

if(BOOST_INCLUDE_DIR AND BOOST_LIBRARY_DIRS)
    set(EMTG_LOCAL_BOOST_LIBRARIES)
    foreach(BOOST_COMPONENT filesystem system serialization)
        file(GLOB EMTG_BOOST_COMPONENT_LIBRARY "${BOOST_LIBRARY_DIRS}/*boost_${BOOST_COMPONENT}*.lib")
        list(LENGTH EMTG_BOOST_COMPONENT_LIBRARY EMTG_BOOST_COMPONENT_LIBRARY_COUNT)
        if(EMTG_BOOST_COMPONENT_LIBRARY_COUNT GREATER 0)
            list(GET EMTG_BOOST_COMPONENT_LIBRARY 0 EMTG_BOOST_COMPONENT_LIBRARY_FIRST)
            list(APPEND EMTG_LOCAL_BOOST_LIBRARIES "${EMTG_BOOST_COMPONENT_LIBRARY_FIRST}")
        else()
            set(EMTG_LOCAL_BOOST_LIBRARIES)
            break()
        endif()
    endforeach()

    if(EMTG_LOCAL_BOOST_LIBRARIES)
        set(Boost_FOUND TRUE)
        set(Boost_INCLUDE_DIRS "${BOOST_INCLUDE_DIR}")
        set(Boost_LIBRARY_DIRS "${BOOST_LIBRARY_DIRS}")
        set(Boost_LIBRARIES ${EMTG_LOCAL_BOOST_LIBRARIES})
        message(STATUS "Using vendored Boost libraries from ${BOOST_LIBRARY_DIRS}")
    endif()
endif()

if(NOT Boost_FOUND)
    include(FindBoost)
    if(BOOST_LIBRARY_DIRS)
        set(BOOST_LIBRARYDIR "${BOOST_LIBRARY_DIRS}")
    endif()
    if(MSVC)
	    Set(Boost_NAMESPACE libboost)
        set(Boost_USE_STATIC_LIBS ON)
        set(Boost_USE_STATIC_RUNTIME OFF)
    endif(MSVC)

    find_package(Boost 1.60 COMPONENTS filesystem system serialization REQUIRED)
endif()

include_directories(${Boost_INCLUDE_DIRS})
link_directories(${Boost_LIBRARY_DIRS})

set(EMTG_LIBRARIES ${EMTG_LIBRARIES} ${Boost_LIBRARIES})

#pull in EMTG itself
add_subdirectory(src)


