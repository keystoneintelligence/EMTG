# Fortran runtime I/O diagnostics do not honor GCC's file-prefix map on all
# supported toolchains. Compile with relative source arguments instead.
set(CMAKE_Fortran_COMPILER_LAUNCHER
    "${CMAKE_COMMAND};-DSOURCE_ROOT=${CMAKE_SOURCE_DIR};-P;${CMAKE_CURRENT_LIST_DIR}/compile-fortran.cmake;--")

# Ninja preprocesses Fortran before the compiler launcher runs. Preserve that
# preprocessing step, but make its line markers use relative filenames too.
if(CMAKE_Fortran_PREPROCESS_SOURCE)
    string(PREPEND CMAKE_Fortran_PREPROCESS_SOURCE
        "\"${CMAKE_COMMAND}\" \"-DSOURCE_ROOT=${CMAKE_SOURCE_DIR}\" -P \"${CMAKE_CURRENT_LIST_DIR}/compile-fortran.cmake\" -- ")
endif()
