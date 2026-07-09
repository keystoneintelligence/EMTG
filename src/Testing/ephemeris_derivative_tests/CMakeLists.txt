set(ephemeris_derivative_tests_executable_files
    ${ephemeris_derivative_tests_executable_files}
    ${CMAKE_CURRENT_SOURCE_DIR}/ephemeris_derivative_tests.cpp)

add_executable(ephemeris_derivative_tests ${ephemeris_derivative_tests_executable_files})
target_include_directories(ephemeris_derivative_tests PRIVATE ../../Astrodynamics)

emtg_add_testbed_test(EMTG.ephemeris_derivative_tests ephemeris_derivative_tests)
