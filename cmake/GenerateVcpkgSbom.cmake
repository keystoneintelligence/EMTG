if(NOT DEFINED STATUS_FILE OR NOT EXISTS "${STATUS_FILE}")
    message(FATAL_ERROR "STATUS_FILE must identify vcpkg's installed status file")
endif()
if(NOT DEFINED OUTPUT OR NOT DEFINED EMTG_VERSION OR NOT DEFINED PLATFORM)
    message(FATAL_ERROR "OUTPUT, EMTG_VERSION, and PLATFORM are required")
endif()

string(TIMESTAMP CREATED "%Y-%m-%dT%H:%M:%SZ" UTC)
file(READ "${STATUS_FILE}" STATUS_CONTENT)
string(REPLACE "\r\n" "\n" STATUS_CONTENT "${STATUS_CONTENT}")
string(REPLACE "\n\n" ";" STATUS_PARAGRAPHS "${STATUS_CONTENT}")

file(WRITE "${OUTPUT}"
"SPDXVersion: SPDX-2.3
DataLicense: CC0-1.0
SPDXID: SPDXRef-DOCUMENT
DocumentName: EMTG-${EMTG_VERSION}-${PLATFORM}
DocumentNamespace: https://github.com/keystoneintelligence/EMTG/sbom/${EMTG_VERSION}/${PLATFORM}
Creator: Tool: EMTG-CMake-build
Created: ${CREATED}

PackageName: EMTG
SPDXID: SPDXRef-Package-EMTG
PackageVersion: ${EMTG_VERSION}
PackageDownloadLocation: NOASSERTION
FilesAnalyzed: false
PackageLicenseConcluded: NOASSERTION
PackageLicenseDeclared: NASA-1.3
PackageCopyrightText: NOASSERTION

Relationship: SPDXRef-DOCUMENT DESCRIBES SPDXRef-Package-EMTG
")

set(RELATIONSHIPS)
foreach(PARAGRAPH IN LISTS STATUS_PARAGRAPHS)
    if(NOT PARAGRAPH MATCHES "(^|\n)Status: install ok installed($|\n)")
        continue()
    endif()
    if(NOT PARAGRAPH MATCHES "(^|\n)Package: ([^\n]+)")
        continue()
    endif()
    set(PACKAGE_NAME "${CMAKE_MATCH_2}")
    if(NOT PARAGRAPH MATCHES "(^|\n)Version: ([^\n]+)")
        continue()
    endif()
    set(PACKAGE_VERSION "${CMAKE_MATCH_2}")
    if(PARAGRAPH MATCHES "(^|\n)Architecture: ([^\n]+)")
        set(PACKAGE_ARCH "${CMAKE_MATCH_2}")
    else()
        set(PACKAGE_ARCH "unknown")
    endif()
    if(PARAGRAPH MATCHES "(^|\n)Feature: ([^\n]+)")
        set(PACKAGE_FEATURE "-${CMAKE_MATCH_2}")
    else()
        set(PACKAGE_FEATURE "")
    endif()
    set(PACKAGE_LABEL "${PACKAGE_NAME}-${PACKAGE_ARCH}${PACKAGE_FEATURE}")
    string(REGEX REPLACE "[^A-Za-z0-9.-]" "-" PACKAGE_ID "${PACKAGE_LABEL}")
    file(APPEND "${OUTPUT}"
"
PackageName: ${PACKAGE_LABEL}
SPDXID: SPDXRef-Package-${PACKAGE_ID}
PackageVersion: ${PACKAGE_VERSION}
PackageDownloadLocation: NOASSERTION
FilesAnalyzed: false
PackageLicenseConcluded: NOASSERTION
PackageLicenseDeclared: NOASSERTION
PackageCopyrightText: NOASSERTION
")
    string(APPEND RELATIONSHIPS
        "Relationship: SPDXRef-Package-EMTG DEPENDS_ON SPDXRef-Package-${PACKAGE_ID}\n")
endforeach()

file(APPEND "${OUTPUT}" "\n${RELATIONSHIPS}")

if(DEFINED COMPILER_RUNTIME_NAME AND DEFINED COMPILER_RUNTIME_VERSION)
    string(REGEX REPLACE "[^A-Za-z0-9.-]" "-" COMPILER_RUNTIME_ID
        "${COMPILER_RUNTIME_NAME}")
    file(APPEND "${OUTPUT}"
"
PackageName: ${COMPILER_RUNTIME_NAME}
SPDXID: SPDXRef-Package-${COMPILER_RUNTIME_ID}
PackageVersion: ${COMPILER_RUNTIME_VERSION}
PackageDownloadLocation: NOASSERTION
FilesAnalyzed: false
PackageLicenseConcluded: NOASSERTION
PackageLicenseDeclared: NOASSERTION
PackageCopyrightText: NOASSERTION

Relationship: SPDXRef-Package-EMTG DEPENDS_ON SPDXRef-Package-${COMPILER_RUNTIME_ID}
")
endif()

message(STATUS "Wrote SPDX dependency SBOM: ${OUTPUT}")
