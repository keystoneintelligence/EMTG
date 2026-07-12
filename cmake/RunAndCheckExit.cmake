if(NOT DEFINED EXECUTABLE OR NOT DEFINED EXPECTED_EXIT)
    message(FATAL_ERROR "EXECUTABLE and EXPECTED_EXIT are required")
endif()

execute_process(
    COMMAND "${EXECUTABLE}" ${ARGUMENTS}
    RESULT_VARIABLE actual_exit
    OUTPUT_VARIABLE standard_output
    ERROR_VARIABLE standard_error)

if(NOT actual_exit EQUAL EXPECTED_EXIT)
    message(FATAL_ERROR
        "Expected exit ${EXPECTED_EXIT}, got ${actual_exit}\n"
        "stdout:\n${standard_output}\n"
        "stderr:\n${standard_error}")
endif()

if(DEFINED EXPECTED_OUTPUT)
    set(combined_output "${standard_output}\n${standard_error}")
    if(NOT combined_output MATCHES "${EXPECTED_OUTPUT}")
        message(FATAL_ERROR
            "Output did not match '${EXPECTED_OUTPUT}'\n"
            "stdout:\n${standard_output}\n"
            "stderr:\n${standard_error}")
    endif()
endif()
