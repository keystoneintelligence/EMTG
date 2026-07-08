# EMTG Testing Commands

## Fast Python Checks

Run the pure Python regression harness and schema checks without launching EMTG:

```powershell
python -m pytest
```

The Python checks also enforce `testatron/test_inventory.json`, which is the source of truth for fast unit folders, slow integration folders, expected-no-truth cases, expected-failure cases, and solver/SPICE requirements.

GitHub installs these dependencies from `requirements-dev.txt`.

## Fast C++ Smoke Checks

Configure and run the registered portable CTest smoke targets with the Visual Studio compiler environment:

```powershell
cmd /c "call F:\Microsoft\vs_build_tools\Common7\Tools\VsDevCmd.bat -arch=x64 && cmake --preset ci-fast && cmake --build --preset ci-fast && ctest --preset ci-fast"
```

The `ci-fast` preset enables `EMTG_PORTABLE_TESTBED_ONLY`, which intentionally avoids `EMTG-Config.cmake`, CSPICE, SNOPT, IPOPT, GSL, and the full `EMTGv9` executable. It currently builds and runs `EMTG.adouble_tests` and `EMTG.missionoptions_testbed`.

GitHub also runs direct compile smoke jobs for `adouble` and `missionoptions` so parser-adjacent code gets checked before the broader CTest lane.

## Testatron Unit Sweep

Run the fast executable smoke lane through EMTG and Comparatron:

```powershell
cd testatron
python testatron.py --smoke -e ..\bin\EMTGv9.exe -p ..\PyEMTG --emtg_quiet_nlp 1
```

Run the checked-in unit regression cases through EMTG and Comparatron:

```powershell
cd testatron
python testatron.py -u -e path\to\EMTGv9.exe -p ..\PyEMTG
```

## Full And Nightly

Use full sweeps only after the fast checks pass:

```powershell
cd testatron
python testatron.py -a -e path\to\EMTGv9.exe -p ..\PyEMTG
```

The asteroid integration folder is marked expected-no-truth until a reviewed `.emtg` baseline is added.

## GitHub Testatron Smoke Asset Strategy

`testatron --smoke` should become a separate GitHub workflow after the EMTG executable and required runtime assets are reproducible in CI. Do not commit generated EMTG binaries, Testatron output, or large SPICE kernels directly to git.

Use this asset model:

1. Keep PR-required CI small and deterministic: Python checks, direct C++ smoke jobs, and `cmake --preset ci-fast`.
2. Add a separate `testatron-smoke` workflow triggered by `workflow_dispatch`, nightly schedule, and eventually pull requests after it is stable.
3. Build `EMTGv9` inside the workflow from a CI-specific CMake preset rather than uploading `bin/EMTGv9.exe`.
4. Publish a versioned GitHub Release asset named like `testatron-smoke-assets-v1.zip`.
5. Put only the minimum files needed by the 16 `SMOKE_TEST_CASES` in that archive: required `testatron/universe/ephemeris_files` kernels, required hardware model files, and any non-generated support files not already tracked.
6. Include a manifest file in the archive with filename, byte size, SHA256, source URL, and license/source notes for each external asset.
7. In the workflow, download the release asset, verify every SHA256 from the manifest, unpack it into `testatron/`, then run:

```bash
cd testatron
python testatron.py --smoke -e ../bin/EMTGv9 -p ../PyEMTG --emtg_quiet_nlp 1
```

Use GitHub cache only as an optimization after checksum verification. Treat the release archive and manifest as the source of truth, not the cache.

Keep the large outer-planet and asteroid kernels, IPOPT/SNOPT-dependent cases, and full `testatron -a` runs in nightly or manual workflows until runtime and flake rate are known. Those should not block ordinary PRs until they are consistently reliable.
