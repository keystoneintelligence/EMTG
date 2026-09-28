"""A passing test run must identify the exact source and packaged executable."""
import importlib.util
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("release_provenance", Path(__file__).resolve().parents[1] / "scripts/release_provenance.py")


@pytest.fixture
def provenance():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                    "-C", str(root), *args], text=True).strip()


@pytest.fixture
def build(tmp_path, provenance):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "VERSION").write_text("9.2.0\n")
    (root / ".gitignore").write_text("_local/\ndist/\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "fixture")
    dist = root / "dist"
    dist.mkdir()
    exe = root / "_local/bin/EMTGv9.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fixture executable")
    (dist / "EMTGv9-windows-x64.exe").write_bytes(exe.read_bytes())
    with zipfile.ZipFile(dist / "EMTG-9.2.0-Windows-AMD64.zip", "w") as archive:
        archive.writestr("EMTG/bin/EMTGv9.exe", exe.read_bytes())
    (dist / "build-toolchain.txt").write_text("compiler=fixture\n")
    snapshot = provenance.source_identity(root)
    receipt = provenance.write_receipt(root, snapshot, dist, exe, "windows-x64", {"capabilities": {"ipopt": True}})
    return root, dist, exe, receipt


def test_verified_receipt_binds_source_standalone_archive_and_native_exe(build, provenance):
    root, dist, exe, receipt = build
    result = provenance.verify_receipt(receipt, root=root, executable=exe)
    assert result["same_source"]
    assert result["build_source_revision"] == git(root, "rev-parse", "HEAD")
    assert result["executable_sha256"] == provenance.digest(exe)
    assert str(root) not in receipt.read_text()
    assert provenance.verify_receipt(receipt)["test_source_revision"] is None


@pytest.mark.parametrize("target", ["native", "standalone", "archive"])
def test_changed_executable_or_archive_fails(build, provenance, target):
    root, dist, exe, receipt = build
    path = exe if target == "native" else dist / ("EMTGv9-windows-x64.exe" if target == "standalone" else "EMTG-9.2.0-Windows-AMD64.zip")
    path.write_bytes(b"other artifact")
    with pytest.raises(ValueError, match="hash|executable"):
        provenance.verify_receipt(receipt, root=root, executable=exe)


def test_new_checkout_cannot_inherit_old_binary_pass(build, provenance):
    root, _, _, receipt = build
    (root / "new.txt").write_text("new source")
    git(root, "add", ".")
    git(root, "commit", "-qm", "new source")
    with pytest.raises(ValueError, match="source"):
        provenance.verify_receipt(receipt, root=root)
    result = provenance.verify_receipt(receipt, root=root, allow_comparison=True)
    assert not result["same_source"]
    assert result["qualification_scope"] == "cross-revision-comparison"


def test_remote_run_revision_must_match_receipt(build, provenance):
    with pytest.raises(ValueError, match="build revision"):
        provenance.verify_receipt(build[3], expected_build_revision="0" * 40)


@pytest.mark.parametrize("dirty", ["tracked", "untracked", "ignored-build-input"])
def test_dirty_source_never_qualifies(build, provenance, dirty):
    root, _, _, receipt = build
    if dirty == "tracked":
        (root / "VERSION").write_text("10.0.0")
    elif dirty == "untracked":
        (root / "custom.cpp").write_text("untracked")
    else:
        (root / ".git/info/exclude").write_text("src/local.cpp\n")
        (root / "src").mkdir()
        (root / "src/local.cpp").write_text("ignored input")
    with pytest.raises(ValueError, match="clean"):
        provenance.verify_receipt(receipt, root=root)


def test_source_changed_during_build_rejected(build, provenance):
    root, dist, exe, _ = build
    before = provenance.source_identity(root)
    (root / "VERSION").write_text("changed")
    with pytest.raises(ValueError, match="during build"):
        provenance.write_receipt(root, before, dist, exe, "windows-x64", {})


def test_archive_executable_must_match_even_with_valid_archive_hash(build, provenance):
    root, dist, exe, _ = build
    with zipfile.ZipFile(dist / "EMTG-9.2.0-Windows-AMD64.zip", "w") as archive:
        archive.writestr("EMTG/bin/EMTGv9.exe", b"stale")
    with pytest.raises(ValueError, match="executable"):
        provenance.write_receipt(root, provenance.source_identity(root), dist, exe, "windows-x64", {})


def test_manifest_path_traversal_rejected(build, provenance):
    receipt = build[3]
    data = json.loads(receipt.read_text())
    data["files"]["../outside"] = "0" * 64
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="path"):
        provenance.verify_receipt(receipt)


def test_missing_receipt_fails_before_solver(tmp_path, provenance):
    with pytest.raises((ValueError, FileNotFoundError)):
        provenance.verify_receipt(tmp_path / "missing.json")


def test_offline_rebuild_cannot_replace_qualified_executable(build, provenance):
    root, dist, exe, receipt = build
    original = json.loads(receipt.read_text())["executable_sha256"]
    exe.write_bytes(b"offline rebuilt different executable")
    with pytest.raises(ValueError, match="hash|executable"):
        provenance.verify_receipt(receipt, root=root, executable=exe)
    assert json.loads(receipt.read_text())["executable_sha256"] == original

def test_github_run_identity_is_bound_to_receipt(build, provenance):
    receipt=build[3]
    data=json.loads(receipt.read_text())
    data["build"]["ci"]={"repository":"example/emtg","run_id":"123","run_attempt":"1"}
    receipt.write_text(json.dumps(data))
    run={"repository":{"full_name":"example/emtg"},"id":123,"run_attempt":1,
         "head_sha":data["source"]["revision"],"path":".github/workflows/release.yml","status":"completed"}
    assert provenance.verify_receipt(receipt,run_metadata=run)["executable_sha256"]
    for key,value in (("head_sha","0"*40),("id",124),("run_attempt",2),("path","other.yml"),("status","in_progress")):
        with pytest.raises(ValueError,match="workflow run"):
            provenance.verify_receipt(receipt,run_metadata={**run,key:value})


@pytest.mark.skipif(__import__("os").name!="nt",reason="PowerShell wrapper")
def test_powershell_propagates_provenance_failure(build):
    import sys
    root,_,exe,receipt=build
    exe.write_bytes(b"wrong executable")
    quote=lambda value:"'"+str(value).replace("'","''")+"'"
    script=Path(__file__).resolve().parents[1]/"scripts/release_provenance.py"
    command=f"& {quote(sys.executable)} {quote(script)} verify --receipt {quote(receipt)} --root {quote(root)} --executable {quote(exe)}; exit $LASTEXITCODE"
    result=subprocess.run(["powershell.exe","-NoProfile","-Command",command],capture_output=True,text=True)
    assert result.returncode==1
    assert "hash differs" in result.stderr


def test_linux_archive_uses_the_same_verifier(tmp_path,provenance):
    import io
    import tarfile
    root=tmp_path/"linux";root.mkdir()
    git(root,"init","-q")
    (root/"VERSION").write_text("9.2.0")
    (root/".gitignore").write_text("dist/\n")
    git(root,"add",".");git(root,"commit","-qm","fixture")
    dist=root/"dist";dist.mkdir()
    exe=dist/"EMTGv9-linux-x64-experimental";exe.write_bytes(b"linux fixture")
    with tarfile.open(dist/"EMTG-9.2.0-Linux-x86_64-experimental.tar.gz","w:gz") as archive:
        entry=tarfile.TarInfo("EMTG/bin/EMTGv9");entry.size=exe.stat().st_size
        archive.addfile(entry,io.BytesIO(exe.read_bytes()))
    receipt=provenance.write_receipt(root,provenance.source_identity(root),dist,exe,"linux-x64-experimental",{})
    assert provenance.verify_receipt(receipt,root=root,executable=exe)["same_source"]
