# EMTG Testing Commands

## Fast Python Checks

Run the pure Python regression harness and schema checks without launching EMTG:

```powershell
python -m pytest
```

The Python checks also enforce `testatron/test_inventory.json`, which is the source of truth for fast unit folders, slow integration folders, expected-no-truth cases, expected-failure cases, and solver/SPICE requirements.

## Fast C++ Smoke Checks

Configure and run the registered CTest smoke targets with the Visual Studio compiler environment:

```powershell
cmd /c "call F:\Microsoft\vs_build_tools\VC\Auxiliary\Build\vcvarsall.bat x64 && cmake -S . -B _local\builds\ctest_smoke -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_EMTG_TESTBED=ON -DRUN_ADOUBLE_TESTBED=ON -DRUN_MISSION_TESTBED=OFF -DRUN_ACCELERATION_MODEL_TESTBED=OFF -DRUN_MISSIONOPTIONS_TESTBED=ON -DUSE_AD_INSTRUMENTATION=OFF -DENABLE_SNOPT=OFF -DENABLE_IPOPT=OFF && cmake --build _local\builds\ctest_smoke --target adouble_tests missionoptions_testbed --config Release && ctest --test-dir _local\builds\ctest_smoke --output-on-failure"
```

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
