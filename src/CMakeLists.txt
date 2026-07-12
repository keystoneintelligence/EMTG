
cmake_policy(SET CMP0003 NEW)

set_property(GLOBAL PROPERTY USE_FOLDERS ON)

# build up the source files
add_subdirectory(Astrodynamics)
add_subdirectory(Core)
add_subdirectory(Executable)
add_subdirectory(HardwareModels)
add_subdirectory(InnerLoop)
add_subdirectory(Integration)
add_subdirectory(Math)
add_subdirectory(Mission)
add_subdirectory(Propagation)
add_subdirectory(Utilities)
add_subdirectory(Scalatron)
if(SPLINE_EPHEM)
    add_subdirectory(SplineEphem)
	include_directories(SplineEphem)
endif(SPLINE_EPHEM)
include_directories(Astrodynamics 
                    Core 
                    Executable
                    HardwareModels 
                    InnerLoop 
                    Integration 
                    Math 
                    Propagation 
                    Utilities
                    Scalatron)

include_directories(Astrodynamics/AccelerationModel
                    Astrodynamics/EquationsOfMotion
                    Astrodynamics/StateRepresentation)
include_directories(Mission 
                    Mission/Journey 
                    Mission/Journey/Phase 
                    Mission/Journey/Phase/TwoPointShootingPhase 
                    Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase
                    Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/FBLT 
                    Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/FBLT/FBLTS
                    Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT 
                    Mission/Journey/Phase/TwoPointShootingPhase/TwoPointShootingLowThrustPhase/MGALT/MGALTS
                    Mission/Journey/Phase/TwoPointShootingPhase/MGAnDSMs
                    Mission/Journey/Phase/TwoPointShootingPhase/MGAnDSMs/ManeuverConstraints
                    Mission/Journey/Phase/TwoPointShootingPhase/MGAnDSMs/ProbeEntryPhase
                    Mission/Journey/Phase/TwoPointShootingPhase/CoastPhase
                    Mission/Journey/Phase/TwoPointShootingPhase/CoastPhase/SundmanCoastPhase
                    Mission/Journey/Phase/TwoPointShootingPhase/CoastPhase/ControlLawThrustPhase
                    Mission/Journey/Phase/ParallelShootingPhase
                    Mission/Journey/Phase/ParallelShootingPhase/PSFBphase
                    Mission/Journey/Phase/ParallelShootingPhase/PSBIphase
                    Mission/Journey/Phase/ParallelShootingPhase/ParallelShootingConstraints
                    Mission/Journey/Phase/BoundaryEvents 
                    Mission/Journey/Phase/BoundaryEvents/Arrival
                    Mission/Journey/Phase/BoundaryEvents/Arrival/EphemerisPeggedArrival
                    Mission/Journey/Phase/BoundaryEvents/Arrival/EphemerisReferencedArrival
                    Mission/Journey/Phase/BoundaryEvents/Arrival/FreePointArrival
                    Mission/Journey/Phase/BoundaryEvents/Arrival/PeriapseArrival
                    Mission/Journey/Phase/BoundaryEvents/Departure
                    Mission/Journey/Phase/BoundaryEvents/Departure/EphemerisPeggedDeparture
                    Mission/Journey/Phase/BoundaryEvents/Departure/EphemerisReferencedDeparture
                    Mission/Journey/Phase/BoundaryEvents/Departure/FreePointDeparture
                    Mission/Journey/Phase/BoundaryEvents/Departure/PeriapseDeparture
                    Mission/Journey/Phase/BoundaryEvents/SpecializedBoundaryConstraints
                    Mission/Journey/Phase/BoundaryEvents/SpecializedBoundaryConstraints/OrbitElementsConstraints
					Mission/Journey/Phase/BoundaryEvents/SpecializedBoundaryConstraints/BoundaryAngleConstraints
                    Mission/ObjectiveFunctions)

set(EMTG_FILES ${EMTG_FILES} ${ASTRODYNAMICS_FILES}
							 ${CORE_FILES}
							 ${HARDWARE_MODEL_FILES}
							 ${INNER_LOOP_FILES}
							 ${INTEGRATION_FILES}
							 ${MATH_FILES}
							 ${MISSION_FILES}
							 ${OUTER_LOOP_FILES}
							 ${PROPAGATION_FILES}
							 ${UTILITIES_FILES}
                             ${SCALATRON_FILES})

if(SPLINE_EPHEM)
	set(EMTG_FILES ${EMTG_FILES} ${SPLINE_EPHEM_FILES})
endif()

# Make source groups for IDEs
set(root_EMTG_src_path ${CMAKE_CURRENT_SOURCE_DIR})
string(REPLACE "/" "\\" windows_src_path_fixed "${root_EMTG_src_path}")
foreach(source IN LISTS EMTG_FILES)
    get_filename_component(source_path "${source}" PATH)
    string(REPLACE "/" "\\" source_path_msvc "${source_path}")
    string(REPLACE "${windows_src_path_fixed}\\" "" source_path_msvc_relative ${source_path_msvc})
    source_group("${source_path_msvc_relative}" FILES "${source}")
endforeach()
source_group("Executable" FILES ${EXECUTABLE_FILES})

# create the EMTG library
add_library(emtg ${EMTG_FILES})
set_property(TARGET emtg PROPERTY CXX_STANDARD 17)
set_property(TARGET emtg PROPERTY POSITION_INDEPENDENT_CODE ON)
target_compile_definitions(emtg PUBLIC BOOST_ALL_NO_LIB)
target_link_libraries(emtg ${EMTG_LIBRARIES})

# create the EMTG executable
add_executable(EMTGv9 ${EXECUTABLE_FILES})

# Fold the GNU C++, GCC, and Fortran runtimes into the standalone executable.
# Ordinary Windows system DLL imports remain dynamic.
emtg_configure_executable(EMTGv9)
target_include_directories(EMTGv9 PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/OuterLoop)
target_compile_definitions(EMTGv9 PRIVATE BOOST_ALL_NO_LIB EMTG_VERSION="${PROJECT_VERSION}")
target_compile_features(EMTGv9 PRIVATE cxx_std_17)
target_link_libraries(EMTGv9 emtg ${EMTG_LIBRARIES})

if(BUILD_TESTING)
    set(EMTG_DOCTOR_FIXTURE_ROOT "${CMAKE_BINARY_DIR}/tests/doctor")
    set(EMTG_DOCTOR_READY_DATA "${EMTG_DOCTOR_FIXTURE_ROOT}/ready")
    set(EMTG_DOCTOR_MISSING_KERNELS_DATA "${EMTG_DOCTOR_FIXTURE_ROOT}/missing-kernels")
    foreach(FIXTURE IN ITEMS "${EMTG_DOCTOR_READY_DATA}" "${EMTG_DOCTOR_MISSING_KERNELS_DATA}")
        file(MAKE_DIRECTORY
            "${FIXTURE}/Universe/ephemeris_files"
            "${FIXTURE}/HardwareModels")
        file(WRITE "${FIXTURE}/Universe/ephemeris_files/naif0012.tls" "test fixture\n")
        file(WRITE "${FIXTURE}/Universe/ephemeris_files/pck00010.tpc" "test fixture\n")
    endforeach()
    file(WRITE "${EMTG_DOCTOR_READY_DATA}/Universe/ephemeris_files/test.bsp" "test fixture\n")

    add_test(NAME EMTG.cli.version COMMAND $<TARGET_FILE:EMTGv9> --version)
    set_tests_properties(EMTG.cli.version PROPERTIES
        PASS_REGULAR_EXPRESSION "EMTG ${PROJECT_VERSION}"
        LABELS "cli;portable")

    add_test(NAME EMTG.cli.capabilities COMMAND $<TARGET_FILE:EMTGv9> --capabilities)
    set_tests_properties(EMTG.cli.capabilities PROPERTIES
        PASS_REGULAR_EXPRESSION "\\\"ipopt\\\""
        LABELS "cli;portable")

    add_test(NAME EMTG.cli.doctor
        COMMAND $<TARGET_FILE:EMTGv9> --doctor --data-dir "${EMTG_DOCTOR_READY_DATA}")
    set_tests_properties(EMTG.cli.doctor PROPERTIES
        PASS_REGULAR_EXPRESSION "Status: ready"
        LABELS "cli;portable")

    add_test(NAME EMTG.cli.doctor_missing_kernels
        COMMAND ${CMAKE_COMMAND}
            -DEXECUTABLE=$<TARGET_FILE:EMTGv9>
            -DEXPECTED_EXIT=3
            "-DARGUMENTS=--doctor;--data-dir;${EMTG_DOCTOR_MISSING_KERNELS_DATA}"
            "-DEXPECTED_OUTPUT=naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/"
            -P "${PROJECT_SOURCE_DIR}/cmake/RunAndCheckExit.cmake")
    set_tests_properties(EMTG.cli.doctor_missing_kernels PROPERTIES LABELS "cli;data")

    add_test(NAME EMTG.cli.mission_missing_kernels
        COMMAND ${CMAKE_COMMAND}
            -DEXECUTABLE=$<TARGET_FILE:EMTGv9>
            -DEXPECTED_EXIT=3
            "-DARGUMENTS=--data-dir;${EMTG_DOCTOR_MISSING_KERNELS_DATA};${PROJECT_SOURCE_DIR}/PyEMTG/default.emtgopt"
            "-DEXPECTED_OUTPUT=required kernels are missing"
            -P "${PROJECT_SOURCE_DIR}/cmake/RunAndCheckExit.cmake")
    set_tests_properties(EMTG.cli.mission_missing_kernels PROPERTIES LABELS "cli;data")

    add_test(NAME EMTG.cli.missing_options
        COMMAND ${CMAKE_COMMAND}
            -DEXECUTABLE=$<TARGET_FILE:EMTGv9>
            -DEXPECTED_EXIT=2
            -P "${PROJECT_SOURCE_DIR}/cmake/RunAndCheckExit.cmake")
    set_tests_properties(EMTG.cli.missing_options PROPERTIES LABELS "cli;portable")
endif()

# if desired, build the testbeds
if(BUILD_EMTG_TESTBED)
	add_subdirectory(Testing)
endif()

# if desired, build the propulator
if(BUILD_PROPULATOR)
    SET(Python_ADDITIONAL_VERSIONS ${PYTHON_VERSIONS_REQUIRED}$ )
    
    FIND_PACKAGE(PythonInterp)

    if (PYTHONINTERP_FOUND)
      if (UNIX AND NOT APPLE)
        if (PYTHON_VERSION_MAJOR EQUAL 3)
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_SUFFIX}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp 3)
            FIND_PACKAGE(PythonLibs 3 REQUIRED)
        else()
            FIND_PACKAGE(Boost COMPONENTS python)
            FIND_PACKAGE(PythonInterp)
            FIND_PACKAGE(PythonLibs REQUIRED)
        endif()
      else()	
        if (PYTHON_VERSION_MAJOR EQUAL 3)
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_MAJOR}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp 3)
            FIND_PACKAGE(PythonLibs 3 REQUIRED)
        else()
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_MAJOR}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp)
            FIND_PACKAGE(PythonLibs REQUIRED)
        endif()
      endif()
    else()
        message("Python not found")
    endif()

    message(STATUS "PYTHON_LIBRARIES = ${PYTHON_LIBRARIES}")
    message(STATUS "PYTHON_EXECUTABLE = ${PYTHON_EXECUTABLE}")
    message(STATUS "PYTHON_INCLUDE_DIRS = ${PYTHON_INCLUDE_DIRS}")
    message(STATUS "Boost_LIBRARIES = ${Boost_LIBRARIES}")

    add_subdirectory(Propulator)
    
    add_executable(PropulatorDriver ${PROPULATOR_FILES})
    emtg_configure_executable(PropulatorDriver)
    target_include_directories(PropulatorDriver PRIVATE)
    target_link_libraries(PropulatorDriver emtg ${EMTG_LIBRARIES})
    
    add_library(propulator SHARED ${PROPULATOR_MODULE_FILES})
    target_include_directories(propulator PRIVATE ${PYTHON_INCLUDE_DIRS} ${Boost_INCLUDE_DIRS})
    #turn on the flag to compile the python interface, and the flag to disable boost auto-link
    target_compile_definitions(propulator PRIVATE PROPULATOR_PYTHON_INTERFACE BOOST_ALL_NO_LIB BOOST_PYTHON_STATIC_LIB)
    target_link_libraries(propulator emtg ${EMTG_LIBRARIES}  ${Boost_LIBRARIES} ${PYTHON_LIBRARIES})
endif()

#if desired, build PyHardware
if(BUILD_PYHARDWARE)
    SET(Python_ADDITIONAL_VERSIONS ${PYTHON_VERSIONS_REQUIRED}$ )
    
    FIND_PACKAGE(PythonInterp)

    if (PYTHONINTERP_FOUND)
      if (UNIX AND NOT APPLE)
        if (PYTHON_VERSION_MAJOR EQUAL 3)
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_SUFFIX}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp 3)
            FIND_PACKAGE(PythonLibs 3 REQUIRED)
        else()
            FIND_PACKAGE(Boost COMPONENTS python)
            FIND_PACKAGE(PythonInterp)
            FIND_PACKAGE(PythonLibs REQUIRED)
        endif()
      else()	
        if (PYTHON_VERSION_MAJOR EQUAL 3)
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_MAJOR}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp 3)
            FIND_PACKAGE(PythonLibs 3 REQUIRED)
        else()
            FIND_PACKAGE(Boost COMPONENTS python${PYTHON_VERSION_MAJOR}${PYTHON_VERSION_MINOR})
            FIND_PACKAGE(PythonInterp)
            FIND_PACKAGE(PythonLibs REQUIRED)
        endif()
      endif()
    else()
        message("Python not found")
    endif()

    message(STATUS "PYTHON_LIBRARIES = ${PYTHON_LIBRARIES}")
    message(STATUS "PYTHON_EXECUTABLE = ${PYTHON_EXECUTABLE}")
    message(STATUS "PYTHON_INCLUDE_DIRS = ${PYTHON_INCLUDE_DIRS}")
    message(STATUS "Boost_LIBRARIES = ${Boost_LIBRARIES}")

    add_subdirectory(Python)
    
    add_library(PyHardware SHARED ${PYHARDWARE_MODULE_FILES})
    target_include_directories(PyHardware PRIVATE ${PYTHON_INCLUDE_DIRS} ${Boost_INCLUDE_DIRS})
    #turn on the flag to compile the python interface, and the flag to disable boost auto-link
    target_compile_definitions(PyHardware PRIVATE PROPULATOR_PYTHON_INTERFACE BOOST_ALL_NO_LIB BOOST_PYTHON_STATIC_LIB)
    target_link_libraries(PyHardware emtg ${EMTG_LIBRARIES}  ${Boost_LIBRARIES} ${PYTHON_LIBRARIES})
    SET_TARGET_PROPERTIES(PyHardware PROPERTIES PREFIX "")
endif()

#ignore default libraries on Windows
if (MSVC)
    SET_TARGET_PROPERTIES(emtg PROPERTIES LINK_FLAGS /NODEFAULTLIB:\"libcmt.lib;libcmtd.lib;msvcrtd.lib\")
endif()

# Dynamic developer builds keep their runtime DLLs beside the build-tree
# executable. Static release profiles leave this list empty.
if(WIN32 AND ENABLE_IPOPT AND EMTG_IPOPT_RUNTIME_DLLS)
    foreach(IPOPT_RUNTIME_DLL IN LISTS EMTG_IPOPT_RUNTIME_DLLS)
        add_custom_command(TARGET EMTGv9 POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${IPOPT_RUNTIME_DLL}" "$<TARGET_FILE_DIR:EMTGv9>")
    endforeach()
endif()

