include_guard(GLOBAL)

function(emtg_configure_python_extension target)
    if(WIN32)
        # Python's Windows loader discovers .pyd modules, not ordinary .dll files.
        set_target_properties(${target} PROPERTIES PREFIX "" SUFFIX ".pyd")
    endif()
endfunction()

function(emtg_stage_snopt_runtime target)
    if(NOT WIN32 OR NOT ENABLE_SNOPT)
        return()
    endif()

    set(EMTG_SNOPT_RUNTIME_DLL "" CACHE FILEPATH
        "Optional licensed SNOPT runtime DLL for a custom Windows layout")
    if(EMTG_SNOPT_RUNTIME_DLL)
        if(NOT EXISTS "${EMTG_SNOPT_RUNTIME_DLL}")
            message(FATAL_ERROR "EMTG_SNOPT_RUNTIME_DLL does not exist: ${EMTG_SNOPT_RUNTIME_DLL}")
        endif()
        set(runtime "${EMTG_SNOPT_RUNTIME_DLL}")
    elseif(SNOPT_MINGW_DLL)
        # Match the legacy import-library layout, including multi-config builds.
        if(CMAKE_CONFIGURATION_TYPES)
            set(runtime "${SNOPTDIR_OVRD}/build/$<CONFIG>/libsnopt.dll")
        else()
            set(runtime "${SNOPTDIR_OVRD}/build/libsnopt.dll")
        endif()
    elseif(EXISTS "${SNOPTDIR_OVRD}/lib/snopt.dll")
        set(runtime "${SNOPTDIR_OVRD}/lib/snopt.dll")
    else()
        # Static SNOPT builds have no runtime DLL to distribute.
        return()
    endif()

    add_custom_command(TARGET ${target} POST_BUILD
        COMMAND ${CMAKE_COMMAND} -E copy_if_different
            "${runtime}" "$<TARGET_FILE_DIR:${target}>"
        VERBATIM)
    install(FILES "${runtime}" DESTINATION "${CMAKE_INSTALL_BINDIR}" COMPONENT Runtime)
endfunction()
