# Running EMTG from a release bundle

## Windows

Download the portable ZIP from GitHub Releases, extract it, and run:

```powershell
.\bin\EMTGv9.exe mission.emtgopt
```

No installation or administrator access is required. Alternatively, download
the standalone EXE and supply its data location explicitly:

```powershell
.\EMTGv9-windows-x64.exe --data-dir C:\path\to\emtg-data mission.emtgopt
```

When the release includes the Studio bundle, launch
`EMTGStudio\EMTGStudio.exe`. It opens the token-protected local workbench in
the default browser and discovers `EMTGv9.exe` and data from the extracted
bundle. No separate Python or Node.js installation is required.

## Linux (experimental)

The Linux tarball has not yet been validated on a clean Linux host and is not
currently a production-supported release target. Extract it anywhere and run
`bin/EMTGv9`; no system installation is provided.

## Runtime data

Portable release bundles include standard Universe definitions, HardwareModels,
examples, and the small NAIF text kernels. Large `.bsp` files are intentionally
not redistributed. Download planetary BSPs from
https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/ and other
mission-specific kernels from https://naif.jpl.nasa.gov/pub/naif/. Place them
in `Universe/ephemeris_files`; the packaged `go_get_these_files.txt` contains
the same links.

EMTG finds data beside a portable bundle. Override discovery with either:

```text
EMTGv9 --data-dir /path/to/emtg-data mission.emtgopt
EMTG_DATA_DIR=/path/to/emtg-data EMTGv9 mission.emtgopt
```

Run `EMTGv9 --doctor` to check solver and data availability.
