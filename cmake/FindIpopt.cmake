# Locate an Ipopt installation and expose it as Ipopt::Ipopt.
#
# Supported hints (in precedence order):
#   IPOPT_INCLUDE_DIR       directory containing IpStdCInterface.h, or its parent
#   IPOPT_LIBRARY           complete path to an import/static/shared library
#   IPOPT_RUNTIME_LIBRARY   complete path to the Windows runtime DLL
#   IPOPT_LIBRARY_DIR       directory containing libraries
#   IPOPT_BIN_DIR           directory containing runtime DLLs
#   IPOPT_ROOT_DIR          installation prefix
#
# IPOPTDIR_OVRD is accepted as a legacy alias for IPOPT_ROOT_DIR.

include(FindPackageHandleStandardArgs)

set(IPOPT_ROOT_DIR "${IPOPT_ROOT_DIR}" CACHE PATH "Ipopt installation prefix")
set(IPOPT_INCLUDE_DIR "${IPOPT_INCLUDE_DIR}" CACHE PATH "Ipopt include directory")
set(IPOPT_LIBRARY "${IPOPT_LIBRARY}" CACHE FILEPATH "Ipopt library or import library")
set(IPOPT_LIBRARY_DIR "${IPOPT_LIBRARY_DIR}" CACHE PATH "Directory containing Ipopt libraries")
set(IPOPT_RUNTIME_LIBRARY "${IPOPT_RUNTIME_LIBRARY}" CACHE FILEPATH "Ipopt runtime DLL")
set(IPOPT_BIN_DIR "${IPOPT_BIN_DIR}" CACHE PATH "Directory containing Ipopt runtime DLLs")
set(IPOPT_MINGW_BIN_DIR "${IPOPT_MINGW_BIN_DIR}" CACHE PATH "Directory containing MinGW runtime DLLs required by Ipopt")

if(NOT IPOPT_ROOT_DIR AND IPOPTDIR_OVRD)
    set(IPOPT_ROOT_DIR "${IPOPTDIR_OVRD}" CACHE PATH "Ipopt installation prefix" FORCE)
endif()

# Prefer an installation-provided CMake package when one is available.
if(Ipopt_FIND_VERSION)
    find_package(Ipopt ${Ipopt_FIND_VERSION} CONFIG QUIET NO_MODULE)
else()
    find_package(Ipopt CONFIG QUIET NO_MODULE)
endif()
if(Ipopt_FOUND)
    if(TARGET Ipopt::ipopt AND NOT TARGET Ipopt::Ipopt)
        add_library(Ipopt::Ipopt INTERFACE IMPORTED)
        set_property(TARGET Ipopt::Ipopt PROPERTY INTERFACE_LINK_LIBRARIES Ipopt::ipopt)
    elseif(TARGET ipopt AND NOT TARGET Ipopt::Ipopt)
        add_library(Ipopt::Ipopt INTERFACE IMPORTED)
        set_property(TARGET Ipopt::Ipopt PROPERTY INTERFACE_LINK_LIBRARIES ipopt)
    endif()

    if(TARGET Ipopt::Ipopt)
        get_target_property(_IPOPT_CONFIG_INCLUDE_DIRS Ipopt::Ipopt INTERFACE_INCLUDE_DIRECTORIES)
        find_path(_IPOPT_CONFIG_C_INCLUDE_DIR
            NAMES IpStdCInterface.h
            HINTS ${_IPOPT_CONFIG_INCLUDE_DIRS} ${IPOPT_INCLUDE_DIR} ${IPOPT_ROOT_DIR}
            PATH_SUFFIXES coin-or coin include/coin-or include/coin)
        if(_IPOPT_CONFIG_C_INCLUDE_DIR)
            set(IPOPT_INCLUDE_DIR "${_IPOPT_CONFIG_C_INCLUDE_DIR}" CACHE PATH
                "Directory containing IpStdCInterface.h" FORCE)
            set_property(TARGET Ipopt::Ipopt APPEND PROPERTY
                INTERFACE_INCLUDE_DIRECTORIES "${_IPOPT_CONFIG_C_INCLUDE_DIR}")
        endif()
        if(DEFINED Ipopt_VERSION)
            set(IPOPT_VERSION "${Ipopt_VERSION}")
        elseif(DEFINED IPOPT_VERSION_STRING)
            set(IPOPT_VERSION "${IPOPT_VERSION_STRING}")
        endif()
        set(IPOPT_FOUND TRUE)
        return()
    endif()
endif()

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(PC_IPOPT QUIET ipopt)
endif()

set(_IPOPT_ROOT_HINTS ${IPOPT_ROOT_DIR} $ENV{IPOPT_ROOT_DIR} $ENV{IPOPT_DIR})
set(_IPOPT_INCLUDE_HINTS ${IPOPT_INCLUDE_DIR} ${PC_IPOPT_INCLUDE_DIRS})
set(_IPOPT_LIBRARY_HINTS ${IPOPT_LIBRARY_DIR} ${PC_IPOPT_LIBRARY_DIRS})
set(_IPOPT_BIN_HINTS ${IPOPT_BIN_DIR})

foreach(_root IN LISTS _IPOPT_ROOT_HINTS)
    if(_root)
        list(APPEND _IPOPT_INCLUDE_HINTS "${_root}/include")
        list(APPEND _IPOPT_LIBRARY_HINTS "${_root}/lib" "${_root}/lib64")
        list(APPEND _IPOPT_BIN_HINTS "${_root}/bin")
    endif()
endforeach()

# Use a separate result variable so a parent include directory supplied through
# IPOPT_INCLUDE_DIR does not suppress the search for the coin-or subdirectory.
find_path(IPOPT_C_INCLUDE_DIR
    NAMES IpStdCInterface.h
    HINTS ${_IPOPT_INCLUDE_HINTS}
    PATH_SUFFIXES coin-or coin include/coin-or include/coin)

if(IPOPT_C_INCLUDE_DIR)
    set(IPOPT_INCLUDE_DIR "${IPOPT_C_INCLUDE_DIR}" CACHE PATH "Directory containing IpStdCInterface.h" FORCE)
endif()

if(IPOPT_INCLUDE_DIR)
    foreach(_config_header IpoptConfig.h ipopt_config.h)
        if(EXISTS "${IPOPT_INCLUDE_DIR}/${_config_header}")
            file(STRINGS "${IPOPT_INCLUDE_DIR}/${_config_header}" _IPOPT_VERSION_LINE
                 REGEX "^#define[ \t]+IPOPT_VERSION[ \t]+\"[0-9]+\\.[0-9]+\\.[0-9]+\"")
            if(_IPOPT_VERSION_LINE MATCHES "\"([0-9]+\\.[0-9]+\\.[0-9]+)\"")
                set(IPOPT_VERSION "${CMAKE_MATCH_1}")
            endif()
            break()
        endif()
    endforeach()
endif()

if(NOT IPOPT_LIBRARY)
    find_library(IPOPT_DISCOVERED_LIBRARY
        NAMES ipopt libipopt
        HINTS ${_IPOPT_LIBRARY_HINTS})
    if(NOT IPOPT_DISCOVERED_LIBRARY AND WIN32)
        find_file(IPOPT_DISCOVERED_LIBRARY
            NAMES libipopt.dll.a ipopt.dll.a
            HINTS ${_IPOPT_LIBRARY_HINTS})
    endif()
    if(IPOPT_DISCOVERED_LIBRARY)
        set(IPOPT_LIBRARY "${IPOPT_DISCOVERED_LIBRARY}" CACHE FILEPATH "Ipopt library or import library" FORCE)
    endif()
endif()

if(WIN32 AND NOT IPOPT_RUNTIME_LIBRARY)
    find_file(IPOPT_DISCOVERED_RUNTIME_LIBRARY
        NAMES libipopt-3.dll libipopt.dll ipopt.dll
        HINTS ${_IPOPT_BIN_HINTS})
    if(IPOPT_DISCOVERED_RUNTIME_LIBRARY)
        set(IPOPT_RUNTIME_LIBRARY "${IPOPT_DISCOVERED_RUNTIME_LIBRARY}" CACHE FILEPATH "Ipopt runtime DLL" FORCE)
    endif()
endif()

set(_IPOPT_LINK_ARTIFACT "${IPOPT_LIBRARY}")

# MSVC cannot consume a GNU .dll.a import library. Generate a small MSVC import
# library from the stable C API exports and verify every required symbol against
# the selected runtime DLL first. This keeps the bridge reproducible and avoids
# a source-tree .def file tied to one developer's working tree.
if(MSVC AND IPOPT_RUNTIME_LIBRARY
   AND (NOT IPOPT_LIBRARY OR IPOPT_LIBRARY MATCHES "\\.dll\\.a$"))
    get_filename_component(_IPOPT_MSVC_TOOL_DIR "${CMAKE_LINKER}" DIRECTORY)
    find_program(_IPOPT_DUMPBIN_EXECUTABLE NAMES dumpbin HINTS "${_IPOPT_MSVC_TOOL_DIR}")
    find_program(_IPOPT_LIB_EXECUTABLE NAMES lib HINTS "${_IPOPT_MSVC_TOOL_DIR}")

    if(NOT _IPOPT_DUMPBIN_EXECUTABLE OR NOT _IPOPT_LIB_EXECUTABLE)
        message(FATAL_ERROR
            "Ipopt was found as a MinGW runtime DLL, but the MSVC dumpbin.exe/lib.exe tools "
            "needed to create an import library were not found. Run CMake from a Visual Studio "
            "developer environment, provide IPOPT_LIBRARY=<path-to-ipopt.lib>, or use a native MSVC Ipopt build.")
    endif()

    execute_process(
        COMMAND "${_IPOPT_DUMPBIN_EXECUTABLE}" /nologo /exports "${IPOPT_RUNTIME_LIBRARY}"
        RESULT_VARIABLE _IPOPT_DUMPBIN_RESULT
        OUTPUT_VARIABLE _IPOPT_EXPORTS
        ERROR_VARIABLE _IPOPT_DUMPBIN_ERROR)
    if(NOT _IPOPT_DUMPBIN_RESULT EQUAL 0)
        message(FATAL_ERROR
            "Failed to inspect Ipopt runtime exports in ${IPOPT_RUNTIME_LIBRARY}.\n${_IPOPT_DUMPBIN_ERROR}")
    endif()

    set(_IPOPT_REQUIRED_C_EXPORTS
        AddIpoptIntOption
        AddIpoptNumOption
        AddIpoptStrOption
        CreateIpoptProblem
        FreeIpoptProblem
        GetIpoptCurrentIterate
        IpoptSolve
        OpenIpoptOutputFile
        SetIntermediateCallback)

    foreach(_symbol IN LISTS _IPOPT_REQUIRED_C_EXPORTS)
        string(FIND "${_IPOPT_EXPORTS}" " ${_symbol}" _IPOPT_SYMBOL_POSITION)
        if(_IPOPT_SYMBOL_POSITION EQUAL -1)
            message(FATAL_ERROR
                "The selected Ipopt runtime ${IPOPT_RUNTIME_LIBRARY} does not export required C API symbol ${_symbol}. "
                "EMTG requires Ipopt 3.14 or newer with its standard C interface enabled.")
        endif()
    endforeach()

    set(_IPOPT_BRIDGE_DIR "${CMAKE_BINARY_DIR}/ipopt_bridge")
    set(_IPOPT_DEF_FILE "${_IPOPT_BRIDGE_DIR}/ipopt_c_api.def")
    set(_IPOPT_IMPORT_LIBRARY "${_IPOPT_BRIDGE_DIR}/ipopt.lib")
    file(MAKE_DIRECTORY "${_IPOPT_BRIDGE_DIR}")
    get_filename_component(_IPOPT_DLL_NAME "${IPOPT_RUNTIME_LIBRARY}" NAME)
    file(WRITE "${_IPOPT_DEF_FILE}" "LIBRARY ${_IPOPT_DLL_NAME}\nEXPORTS\n")
    foreach(_symbol IN LISTS _IPOPT_REQUIRED_C_EXPORTS)
        file(APPEND "${_IPOPT_DEF_FILE}" "    ${_symbol}\n")
    endforeach()

    execute_process(
        COMMAND "${_IPOPT_LIB_EXECUTABLE}" /nologo /def:${_IPOPT_DEF_FILE} /machine:x64 /out:${_IPOPT_IMPORT_LIBRARY}
        RESULT_VARIABLE _IPOPT_LIB_RESULT
        OUTPUT_VARIABLE _IPOPT_LIB_OUTPUT
        ERROR_VARIABLE _IPOPT_LIB_ERROR)
    if(NOT _IPOPT_LIB_RESULT EQUAL 0)
        message(FATAL_ERROR
            "Failed to generate the MSVC Ipopt import library.\n${_IPOPT_LIB_OUTPUT}\n${_IPOPT_LIB_ERROR}")
    endif()
    set(_IPOPT_LINK_ARTIFACT "${_IPOPT_IMPORT_LIBRARY}")
endif()

find_package_handle_standard_args(Ipopt
    REQUIRED_VARS IPOPT_INCLUDE_DIR _IPOPT_LINK_ARTIFACT
    VERSION_VAR IPOPT_VERSION
    FAIL_MESSAGE
        "Could not find a usable Ipopt C interface. Set IPOPT_ROOT_DIR, or set IPOPT_INCLUDE_DIR and IPOPT_LIBRARY explicitly. On Windows also set IPOPT_RUNTIME_LIBRARY or IPOPT_BIN_DIR when using a DLL.")

set(IPOPT_FOUND ${Ipopt_FOUND})

if(Ipopt_FOUND AND NOT TARGET Ipopt::Ipopt)
    if(WIN32 AND IPOPT_RUNTIME_LIBRARY)
        add_library(Ipopt::Ipopt SHARED IMPORTED)
    else()
        add_library(Ipopt::Ipopt UNKNOWN IMPORTED)
    endif()
    set_property(TARGET Ipopt::Ipopt PROPERTY INTERFACE_INCLUDE_DIRECTORIES "${IPOPT_INCLUDE_DIR}")
    if(WIN32 AND IPOPT_RUNTIME_LIBRARY)
        set_property(TARGET Ipopt::Ipopt PROPERTY IMPORTED_IMPLIB "${_IPOPT_LINK_ARTIFACT}")
        set_property(TARGET Ipopt::Ipopt PROPERTY IMPORTED_LOCATION "${IPOPT_RUNTIME_LIBRARY}")
    else()
        set_property(TARGET Ipopt::Ipopt PROPERTY IMPORTED_LOCATION "${_IPOPT_LINK_ARTIFACT}")
        if(PC_IPOPT_LINK_LIBRARIES)
            set_property(TARGET Ipopt::Ipopt PROPERTY INTERFACE_LINK_LIBRARIES "${PC_IPOPT_LINK_LIBRARIES}")
        endif()
    endif()
endif()

set(IPOPT_RUNTIME_DLLS)
if(WIN32 AND IPOPT_RUNTIME_LIBRARY)
    get_filename_component(_IPOPT_RUNTIME_DIR "${IPOPT_RUNTIME_LIBRARY}" DIRECTORY)
    list(APPEND IPOPT_RUNTIME_DLLS "${IPOPT_RUNTIME_LIBRARY}")
    file(GLOB _IPOPT_MUMPS_RUNTIME_DLLS "${_IPOPT_RUNTIME_DIR}/libcoinmumps-*.dll")
    list(APPEND IPOPT_RUNTIME_DLLS ${_IPOPT_MUMPS_RUNTIME_DLLS})
    if(IPOPT_MINGW_BIN_DIR AND IS_DIRECTORY "${IPOPT_MINGW_BIN_DIR}")
        foreach(_runtime_name
                libblas.dll
                liblapack.dll
                libmetis.dll
                libgcc_s_seh-1.dll
                libgfortran-5.dll
                libquadmath-0.dll
                libstdc++-6.dll
                libwinpthread-1.dll)
            if(EXISTS "${IPOPT_MINGW_BIN_DIR}/${_runtime_name}")
                list(APPEND IPOPT_RUNTIME_DLLS "${IPOPT_MINGW_BIN_DIR}/${_runtime_name}")
            endif()
        endforeach()
    endif()
    list(REMOVE_DUPLICATES IPOPT_RUNTIME_DLLS)
endif()

mark_as_advanced(
    IPOPT_C_INCLUDE_DIR
    IPOPT_DISCOVERED_LIBRARY
    IPOPT_DISCOVERED_RUNTIME_LIBRARY
    _IPOPT_DUMPBIN_EXECUTABLE
    _IPOPT_LIB_EXECUTABLE)
