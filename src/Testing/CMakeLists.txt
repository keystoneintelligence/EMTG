cmake_minimum_required(VERSION 3.25)

set(CMAKE_CXX_STANDARD 17)

#name the project, trigger the compiler search

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

project(EMTG_TESTBED)

option(RUN_ACCELERATION_MODEL_TESTBED "Do you want to run the acceleration model testbed?" OFF)
option(RUN_MISSION_TESTBED "Do you want to run the mission testbed?" ON)
option(RUN_MISSIONOPTIONS_TESTBED "Do you want to run the missionoptions testbed?" ON)
option(RUN_ADOUBLE_TESTBED "Do you want to run the local adouble compatibility tests?" ON)
option(RUN_INTEGRATION_TESTBED "Do you want to run the local integration tests?" ON)
option(RUN_EPHEMERIS_DERIVATIVE_TESTBED "Do you want to run the ephemeris derivative quality tests?" ON)
option(RUN_IPOPT_INTERFACE_TESTBED "Do you want to run the analytic IPOPT interface tests?" OFF)
option(RUN_SPLINE_TESTBED "Do you want to run the dependency-free spline tests?" ON)
option(RUN_EPHEMERIS_READER_TESTBED "Do you want to test ephemeris reader spline integration and state layout?" ON)
option(USE_AD_INSTRUMENTATION "Do you want to perform derivative accuracy checks with AD?" ON)

set(EMTG_TESTBED_DEFAULT_OPTIONS "${CMAKE_SOURCE_DIR}/PyEMTG/default.emtgopt" CACHE FILEPATH "Default options file passed to CTest-registered EMTG testbed executables")
set(EMTG_TESTBED_WORKING_DIRECTORY "${CMAKE_BINARY_DIR}" CACHE PATH "Working directory for CTest-registered EMTG testbed executables")
file(MAKE_DIRECTORY "${EMTG_TESTBED_WORKING_DIRECTORY}/tests")

function(emtg_add_testbed_test test_name target_name)
    if(BUILD_TESTING)
        add_test(NAME ${test_name}
                 COMMAND $<TARGET_FILE:${target_name}> "${EMTG_TESTBED_DEFAULT_OPTIONS}")
        set_tests_properties(${test_name}
                             PROPERTIES
                             WORKING_DIRECTORY "${EMTG_TESTBED_WORKING_DIRECTORY}"
                             LABELS "testbed")
    endif()
endfunction()

if (USE_AD_INSTRUMENTATION AND (RUN_MISSION_TESTBED OR RUN_ACCELERATION_MODEL_TESTBED))
    set(TESTBED_LIBRARY ON)

    add_definitions(-DAD_INSTRUMENTATION)
    # GSAD location
    include_directories(${GSAD_PATH})
    
endif()

if (TESTBED_LIBRARY)
    # build up the source files
    add_subdirectory(../Astrodynamics ${CMAKE_BINARY_DIR}/TESTBED/Astrodynamics)
    add_subdirectory(../Core ${CMAKE_BINARY_DIR}/TESTBED/Core)
    add_subdirectory(../Executable ${CMAKE_BINARY_DIR}/TESTBED/Executable)
    add_subdirectory(../HardwareModels ${CMAKE_BINARY_DIR}/TESTBED/HardwareModels)
    add_subdirectory(../Integration ${CMAKE_BINARY_DIR}/TESTBED/Integration)
    add_subdirectory(../Math ${CMAKE_BINARY_DIR}/TESTBED/Math)
    add_subdirectory(../Mission ${CMAKE_BINARY_DIR}/TESTBED/Mission)
    add_subdirectory(../Propagation ${CMAKE_BINARY_DIR}/TESTBED/Propagation)
    add_subdirectory(../Utilities ${CMAKE_BINARY_DIR}/TESTBED/Utilities)
    add_subdirectory(../Scalatron ${CMAKE_BINARY_DIR}/TESTBED/Scalatron)
    if(SPLINE_EPHEM)
        add_subdirectory(../SplineEphem ${CMAKE_BINARY_DIR}/TESTBED/SplineEphem)
        include_directories(../SplineEphem)
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
                        Astrodynamics/EquationsOfMotion)
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
    
    if(HAS_PROBEENTRYPHASE)
        include_directories(Mission/Journey/Phase/TwoPointShootingPhase/MGAnDSMs/ProbeEntryPhase)
    endif()

    set(TESTBED_FILES ${TESTBED_FILES} ${ASTRODYNAMICS_FILES}
                                       ${CORE_FILES}
                                       ${HARDWARE_MODEL_FILES}
                                       ${INTEGRATION_FILES}
                                       ${KEPLER_FILES}
                                       ${MATH_FILES}
                                       ${MISSION_FILES}
                                       ${PROPAGATION_FILES}
                                       ${UTILITIES_FILES}
                                       ${SCALATRON_FILES})

    if(SPLINE_EPHEM)
        set(TESTBED_FILES ${TESTBED_FILES} ${SPLINE_EPHEM_FILES})
    endif()

    # Make source groups for IDEs
    set(root_EMTG_src_path ${CMAKE_CURRENT_SOURCE_DIR})
    string(REPLACE "/" "\\" windows_src_path_fixed "${root_EMTG_src_path}")
    foreach(source IN LISTS TESTBED_FILES)
        get_filename_component(source_path "${source}" PATH)
        string(REPLACE "/" "\\" source_path_msvc "${source_path}")
        string(REPLACE "${windows_src_path_fixed}\\" "" source_path_msvc_relative ${source_path_msvc})
        source_group("${source_path_msvc_relative}" FILES "${source}")
    endforeach()

    set(TESTBED_DEPENDENCIES ${TESTBED_DEPENDENCIES} 
                             ${Boost_LIBRARIES}
                             ${CSPICE_LIBRARY}) #${Boost_LIBRARIES} is set by findBoost

    # create the EMTG library
    
    add_library(testbed_library ${TESTBED_FILES})
    set_property(TARGET testbed_library PROPERTY CXX_STANDARD 17)
    target_compile_definitions(testbed_library PUBLIC BOOST_ALL_NO_LIB)
    target_link_libraries(testbed_library ${TESTBED_DEPENDENCIES})
    #target_compile_definitions(testbed_library PRIVATE AD_INSTRUMENTATION)
endif()

if (RUN_ACCELERATION_MODEL_TESTBED)
    add_subdirectory(acceleration_model_tests)
endif()

if (RUN_ADOUBLE_TESTBED)
    add_subdirectory(adouble_tests)
endif()

if (RUN_INTEGRATION_TESTBED)
    add_subdirectory(integration_tests)
endif()

if (RUN_EPHEMERIS_DERIVATIVE_TESTBED)
    add_subdirectory(ephemeris_derivative_tests)
endif()

if (RUN_MISSION_TESTBED)
    add_subdirectory(mission_tests)
endif()

if (RUN_MISSIONOPTIONS_TESTBED)
    add_subdirectory(missionoptions_tests)
endif()

if (RUN_IPOPT_INTERFACE_TESTBED)
    if(NOT ENABLE_IPOPT OR NOT TARGET emtg)
        message(FATAL_ERROR "RUN_IPOPT_INTERFACE_TESTBED requires a full build with ENABLE_IPOPT=ON")
    endif()
    add_subdirectory(ipopt_interface_tests)
endif()

if (RUN_SPLINE_TESTBED)
    add_executable(natural_cubic_spline_tests spline_tests/natural_cubic_spline_tests.cpp)
    emtg_configure_executable(natural_cubic_spline_tests)
    target_include_directories(natural_cubic_spline_tests PRIVATE "${CMAKE_SOURCE_DIR}/src/Math")
    target_compile_features(natural_cubic_spline_tests PRIVATE cxx_std_17)
    if(BUILD_TESTING)
        add_test(NAME natural_cubic_spline COMMAND natural_cubic_spline_tests)
        set_tests_properties(natural_cubic_spline PROPERTIES
            LABELS "portable;spline"
            TIMEOUT 15)
    endif()
endif()

if (RUN_EPHEMERIS_READER_TESTBED AND TARGET emtg)
    add_executable(ephemeris_reader_state_tests
        ephemeris_reader_tests/ephemeris_reader_state_tests.cpp)
    emtg_configure_executable(ephemeris_reader_state_tests)
    target_include_directories(ephemeris_reader_state_tests PRIVATE
        "${CMAKE_SOURCE_DIR}/src/Math"
        "${CMAKE_SOURCE_DIR}/src/Utilities")
    target_compile_features(ephemeris_reader_state_tests PRIVATE cxx_std_17)
    target_link_libraries(ephemeris_reader_state_tests PRIVATE emtg)
    if(BUILD_TESTING)
        add_test(NAME EMTG.ephemeris_reader_state_layout
                 COMMAND ephemeris_reader_state_tests)
        set_tests_properties(EMTG.ephemeris_reader_state_layout PROPERTIES
            LABELS "ephemeris_reader;regression;spline"
            TIMEOUT 15)
    endif()
endif()
