import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    "release_path_audit", Path(__file__).resolve().parents[1]/"scripts/audit-release-paths.py"
)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


@pytest.mark.parametrize("value", [
    b"C:/Users/developer/project/src/file.cpp",
    b"F:\\private\\project\\file.cpp",
    b"/home/developer/project/file.cpp",
    b"/work/build/src/file.cpp",
    b"cache/_local/tools/lib.f",
    "C:\\Users\\developer\\file.cpp".encode("utf-16-le"),
])
def test_detects_embedded_machine_paths(value):
    assert audit.contains_local_path(b"\0binary\0" + value + b"\0")


def test_keeps_relative_diagnostics_and_public_urls():
    assert not audit.contains_local_path(
        b"EMTG/src/Core/problem.cpp\0lapack/SRC/xerbla.f\0https://github.com/nasa/EMTG"
    )
    assert audit.contains_local_path(b"/mnt/build-host/source/file.cpp", ["/mnt/build-host"])


def test_nasa_installation_defaults_are_exact_and_cannot_hide_build_roots():
    for default in audit.UPSTREAM_DEFAULTS:
        assert not audit.contains_local_path(b"\0" + default + b"\0")
        assert audit.contains_local_path(default, [default.decode()])
        assert audit.contains_local_path(default + b"/private/file.cpp\0")
    assert audit.contains_local_path(b"C:/Users/developer/private/file.cpp\0")


def test_instruction_fragments_are_not_complete_filenames():
    # GNU optimized comparisons of the historical C:/emtg option defaults.
    instructions = b"\x48\xb8C:/emtg/\x48\xbaUniverse\x48\x33\x01"
    assert not audit.contains_local_path(instructions)
    assert audit.contains_local_path(instructions, ["C:/emtg"])
    assert audit.contains_local_path(instructions + b"\0F:/private/build/source.cpp\0")


@pytest.mark.parametrize("kind", ["zip", "tar.gz"])
def test_scans_archive_payload_without_extracting(tmp_path, kind):
    path = tmp_path / ("bundle." + kind)
    payload = b"\0/home/developer/build/file.cpp\0"
    if kind == "zip":
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("bin/solver", payload)
    else:
        with tarfile.open(path, "w:gz") as archive:
            member = tarfile.TarInfo("bin/solver")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
    assert audit.audit_file(path) == ["bin/solver"]


def test_missing_or_empty_requested_release_fails(tmp_path):
    for path in (tmp_path/"missing", tmp_path):
        with pytest.raises(SystemExit) as error:
            audit.main([str(path)])
        assert error.value.code == 2


def test_scans_nested_document_metadata(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as document:
        document.writestr("metadata.xml", b"/home/developer/source/file.cpp")
    path = tmp_path/"bundle.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data/document.xlsx", inner.getvalue())
    assert audit.audit_file(path) == ["data/document.xlsx!metadata.xml"]


def test_upstream_example_exemption_is_exact_and_cannot_hide_a_forbidden_root(tmp_path):
    original = Path(__file__).resolve().parents[1]/"HardwareModels/default.emtg_propulsionsystemopt"
    assert audit.audit_file(original) == []
    assert audit.audit_file(original, ["c:/emtg"])
    changed = tmp_path/original.name
    changed.write_bytes(original.read_bytes() + b"\n# C:/Users/developer/build/file.cpp\n")
    assert audit.audit_file(changed)


def test_fortran_runtime_filename_is_relative(tmp_path):
    if not all(shutil.which(tool) for tool in ("cmake", "ninja", "gfortran")):
        pytest.skip("Fortran path qualification requires CMake, Ninja and GNU Fortran")
    repository = Path(__file__).resolve().parents[1]
    source, build = tmp_path/"private source", tmp_path/"separate build"
    source.mkdir()
    (source/"probe.f90").write_text("program probe\nprint *, 'probe passed'\nend program\n")
    (source/"CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.25)\nproject(probe LANGUAGES Fortran)\n"
        "add_executable(probe probe.f90)\n"
        # Match the managed release: prebuilt MinGW runtimes can carry debug
        # filenames in COFF symbols. Strip those, but retain runtime strings.
        "target_link_options(probe PRIVATE $<$<CONFIG:Release>:-s>)\n"
    )
    subprocess.run([
        "cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_Fortran_COMPILER="+shutil.which("gfortran"),
        "-DCMAKE_USER_MAKE_RULES_OVERRIDE_Fortran="+str(repository/"cmake/vcpkg-overlays/lapack-reference/relative-fortran.cmake"),
    ], check=True, capture_output=True)
    subprocess.run(["cmake", "--build", str(build)], check=True, capture_output=True)
    executable = build/("probe.exe" if (build/"probe.exe").exists() else "probe")
    result = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
    assert "probe passed" in result.stdout
    assert b"probe.f90" in executable.read_bytes()
    assert audit.audit_file(executable, [str(source), str(build)]) == [], (
        audit.LOCAL_PATH.findall(executable.read_bytes().replace(b"\\", b"/"))[:8]
    )
