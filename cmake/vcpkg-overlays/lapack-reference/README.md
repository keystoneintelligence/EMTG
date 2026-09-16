This overlay preserves the `lapack-reference` port from the vcpkg revision
pinned in `cmake/vcpkg-revision.txt`. The only build change passes relative
Fortran source paths to the compiler so runtime diagnostics do not expose the build
machine's directories. LAPACK sources, patches, and scientific compiler
options are otherwise unchanged.

The vcpkg port files are covered by `LICENSE.vcpkg.txt`. The copied
`FindLAPACK.cmake` module retains its CMake BSD license notice; see
`Copyright.txt` for that license.
