# EMTG third-party notices

EMTG packages use the following third-party software. The dependency lock used
for a release records the exact versions and source checksums.

- Boost Software License 1.0 - header-only Boost libraries used by EMTG.
- CSPICE - NASA Navigation and Ancillary Information Facility SPICE Toolkit.
  See https://naif.jpl.nasa.gov/naif/rules.html.
- Ipopt - Eclipse Public License 2.0. See https://coin-or.github.io/Ipopt/LICENSE.html.
- MUMPS - CeCILL-C license. See https://mumps-solver.org/.
- Reference LAPACK and OpenBLAS - BSD-compatible licenses. Exact notices are
  included under the package's `licenses/third-party` directory.
- GCC/MinGW-w64 compiler runtimes - the standalone Windows executable contains
  statically linked GNU and MinGW-w64 runtime code, including libgcc,
  libstdc++, libgfortran/libquadmath as required by the solver dependency
  graph, and MinGW-w64 runtime support. The package includes the exact notices
  supplied by the pinned toolchain under `licenses/compiler-runtime`, including
  GPLv3, GCC Runtime Library Exception 3.1, LGPL, and MinGW-w64 notices as
  applicable. The GCC Runtime Library Exception permits eligible non-GPL
  applications to use the covered GCC runtime files.

SNOPT is not included in public EMTG packages. Licensed users may supply it to
an opt-in local build.

EMTG Studio additionally uses MIT-licensed React, React DOM, Three.js,
React Three Fiber, Drei, TanStack Table, Vite, and their bundled JavaScript
runtime dependencies. Its Python service uses FastAPI and Uvicorn under the
MIT license, Pydantic under the MIT license, and PyInstaller under GPLv2 with
the PyInstaller bootloader exception. Exact JavaScript versions are recorded
in `PyEMTG/Studio/frontend/package-lock.json`; Python version ranges are
recorded in `requirements-studio.txt`.
