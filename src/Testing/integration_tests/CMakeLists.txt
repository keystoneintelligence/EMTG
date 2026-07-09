set(integration_tests_executable_files
    ${CMAKE_CURRENT_SOURCE_DIR}/adaptive_rk_error_control_tests.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Math/EMTG_Matrix.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/Integrand.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/IntegrationCoefficients.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/IntegrationScheme.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/ExplicitRungeKutta.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/RungeKutta4Tableau.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/RungeKuttaDP87Tableau.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Integration/RungeKuttaTableau.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Propagation/PropagatorBase.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Propagation/IntegratedPropagator.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Propagation/IntegratedAdaptiveStepPropagator.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../Propagation/IntegratedFixedStepPropagator.cpp)

add_executable(integration_tests ${integration_tests_executable_files})
target_include_directories(integration_tests PRIVATE ../../Astrodynamics ../../Core ../../Math ../../Integration ../../Propagation)
target_compile_definitions(integration_tests PRIVATE BOOST_ALL_NO_LIB)

emtg_add_testbed_test(EMTG.integration_tests integration_tests)
