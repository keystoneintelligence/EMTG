add_executable(ipopt_interface_tests ipopt_interface_tests.cpp)
set_property(TARGET ipopt_interface_tests PROPERTY CXX_STANDARD 17)
target_compile_definitions(ipopt_interface_tests PRIVATE BOOST_ALL_NO_LIB)
target_link_libraries(ipopt_interface_tests emtg ${EMTG_LIBRARIES})

if(WIN32)
    foreach(_runtime_dll ${EMTG_IPOPT_RUNTIME_DLLS})
        add_custom_command(TARGET ipopt_interface_tests POST_BUILD
            COMMAND ${CMAKE_COMMAND} -E copy_if_different
                "${_runtime_dll}"
                "$<TARGET_FILE_DIR:ipopt_interface_tests>")
    endforeach()
endif()

if(BUILD_TESTING)
    add_test(NAME EMTG.ipopt_interface_tests COMMAND $<TARGET_FILE:ipopt_interface_tests>)
    set_tests_properties(EMTG.ipopt_interface_tests PROPERTIES LABELS "ipopt;solver;testbed")
endif()
