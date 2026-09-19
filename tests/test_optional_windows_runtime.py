"""Exercise optional Windows naming/staging without proprietary solver inputs.

These build fixtures test CMake's output/install behavior, not licensed SNOPT
execution or the Python/Boost.Python ABI of the real extension modules.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("layout", ["multi", "single", "legacy", "override", "static", "disabled"])
def test_optional_windows_outputs(tmp_path, layout):
    cmake = shutil.which("cmake")
    compiler = os.environ.get("CC") or shutil.which("gcc") or shutil.which("clang")
    if not cmake or not compiler or not shutil.which("ninja"):
        pytest.skip("CMake, Ninja and a C compiler are required for the build fixture")
    source = tmp_path / "source"
    source.mkdir()
    solver = tmp_path / "licensed solver"
    paths = {
        "multi": solver / "build/Release/libsnopt.dll",
        "single": solver / "build/libsnopt.dll",
        "legacy": solver / "lib/snopt.dll",
        "override": solver / "custom/custom-snopt.dll",
        "disabled": solver / "lib/snopt.dll",
    }
    runtime = paths.get(layout)
    if runtime:
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"runtime staging fixture, not a licensed SNOPT binary")
    (source / "main.c").write_text("int main(void) { return 0; }\n")
    (source / "module.c").write_text("int fixture(void) { return 1; }\n")
    (source / "CMakeLists.txt").write_text(f'''
cmake_minimum_required(VERSION 3.25)
project(OptionalRuntimeFixture C)
include(GNUInstallDirs)
# Exercise Windows CMake policy on any host without changing its compiler ABI.
set(WIN32 TRUE)
set(ENABLE_SNOPT {"OFF" if layout == "disabled" else "ON"})
set(SNOPT_MINGW_DLL {"ON" if layout in ("multi", "single") else "OFF"})
set(SNOPTDIR_OVRD "{solver.as_posix()}")
include("{(ROOT / 'cmake/OptionalWindowsRuntime.cmake').as_posix()}")
add_executable(driver main.c)
emtg_stage_snopt_runtime(driver)
install(TARGETS driver RUNTIME DESTINATION bin)
foreach(module propulator PyHardware)
    add_library(${{module}} SHARED module.c)
    emtg_configure_python_extension(${{module}})
    install(TARGETS ${{module}} RUNTIME DESTINATION modules LIBRARY DESTINATION modules)
endforeach()
''')
    build = tmp_path / "build"
    install = tmp_path / "install"
    configure = [cmake, "-S", str(source), "-B", str(build), "-G",
                 "Ninja Multi-Config" if layout == "multi" else "Ninja",
                 f"-DCMAKE_C_COMPILER={compiler}", f"-DCMAKE_INSTALL_PREFIX={install}"]
    if layout == "override":
        configure.append(f"-DEMTG_SNOPT_RUNTIME_DLL={runtime}")
    for command in (configure, [cmake, "--build", str(build), "--config", "Release"],
                    [cmake, "--install", str(build), "--config", "Release"]):
        result = subprocess.run(command, text=True, capture_output=True)
        assert result.returncode == 0, result.stdout + result.stderr
    built = build / "Release" if layout == "multi" else build
    for name in ("propulator", "PyHardware"):
        assert (built / f"{name}.pyd").is_file()
        assert (install / "modules" / f"{name}.pyd").is_file()
    if runtime and layout != "disabled":
        assert (built / runtime.name).read_bytes() == runtime.read_bytes()
        assert (install / "bin" / runtime.name).read_bytes() == runtime.read_bytes()
    else:
        assert not list((install / "bin").glob("*.dll"))
        assert not list(built.glob("*snopt*.dll"))
    if layout == "override":
        result = subprocess.run(configure + [f"-DEMTG_SNOPT_RUNTIME_DLL={solver / 'missing.dll'}"],
                                text=True, capture_output=True)
        assert result.returncode != 0
        assert "EMTG_SNOPT_RUNTIME_DLL does not exist" in result.stdout + result.stderr
