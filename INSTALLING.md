# Running EMTG from a release bundle

Select an EMTG Community Edition release from the [Releases page](https://github.com/keystoneintelligence/EMTG/releases).
Windows x64 is the primary CLI target; Linux artifacts are experimental. Read the
release notes and [SUPPORT.md](SUPPORT.md) for the scope actually qualified.

## Windows

Download and extract the Windows ZIP when its release assets are available.
For source builds, use [BUILDING.md](BUILDING.md). Keep the downloaded checksum
files, dependency notices, and provenance receipt with the distribution.
Use a short extraction and mission-output path. Run:

```powershell
.\bin\EMTGv9.exe mission.emtgopt
```

No installation or administrator access is required. Alternatively, download
the standalone EXE and supply its data location explicitly:

```powershell
.\EMTGv9-windows-x64.exe --data-dir C:\path\to\emtg-data mission.emtgopt
```

## Linux (experimental)

The Linux tarball is experimental; managed release qualification covers Ubuntu
22.04 x64 only. See [qualification scope](docs/1_Developers/qualification.md).
Extract it and run `bin/EMTGv9`; no system installation is provided.

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

Run `EMTGv9 --doctor` to check solver and data availability. Exit code `3` reports
that action is required, including when BSP kernels have not been supplied.
Data discovery does not verify that the selected kernels are appropriate for
the mission; record their exact hashes and avoid unintended overlapping inputs.
