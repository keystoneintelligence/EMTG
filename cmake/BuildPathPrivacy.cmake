include_guard(DIRECTORY)

# Release diagnostics retain useful source locations without recording the
# checkout or dependency-cache location. Debug builds keep debugger paths.
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
    set(EMTG_PATH_ROOTS "${CMAKE_SOURCE_DIR}" "${CMAKE_BINARY_DIR}")
    set(EMTG_PATH_LABELS EMTG EMTG-build)
    if(DEFINED ENV{VCPKG_ROOT})
        list(APPEND EMTG_PATH_ROOTS "$ENV{VCPKG_ROOT}")
        list(APPEND EMTG_PATH_LABELS dependencies)
    endif()
    list(LENGTH EMTG_PATH_ROOTS EMTG_PATH_COUNT)
    math(EXPR EMTG_PATH_LAST "${EMTG_PATH_COUNT} - 1")
    foreach(index RANGE ${EMTG_PATH_LAST})
        list(GET EMTG_PATH_ROOTS ${index} root)
        list(GET EMTG_PATH_LABELS ${index} label)
        file(TO_CMAKE_PATH "${root}" root)
        add_compile_options(
            "$<$<AND:$<CONFIG:Release>,$<COMPILE_LANGUAGE:C,CXX>>:-ffile-prefix-map=${root}=${label}>")
    endforeach()
endif()
