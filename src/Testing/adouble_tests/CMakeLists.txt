set(adouble_tests_executable_files ${adouble_tests_executable_files}
                                   ${CMAKE_CURRENT_SOURCE_DIR}/adouble_tests.cpp
                                   ${CMAKE_CURRENT_SOURCE_DIR}/../../Math/EMTG_Matrix.cpp)

add_executable(adouble_tests ${adouble_tests_executable_files})
emtg_configure_executable(adouble_tests)
target_include_directories(adouble_tests PRIVATE ../../Core ../../Math)
target_compile_definitions(adouble_tests PRIVATE AD_INSTRUMENTATION)

emtg_add_testbed_test(EMTG.adouble_tests adouble_tests)
