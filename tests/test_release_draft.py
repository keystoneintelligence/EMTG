"""Publication needs both verified platform bundles and preserves final review."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import zipfile

import pytest


@pytest.fixture
def publisher(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("community_release_draft", scripts / "prepare_github_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                    "-C", str(root), *args], text=True).strip()


@pytest.fixture
def bundles(tmp_path, publisher):
    import release_provenance as provenance
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "VERSION").write_text("9.2.0\n")
    (root / ".gitignore").write_text("_local/\n")
    notes = root / "docs/releases/9.2.0.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Reviewed announcement")
    git(root, "add", ".")
    git(root, "commit", "-qm", "source fixture")
    git(root, "tag", "v9.2.0")
    artifacts = root / "_local/release"
    artifacts.mkdir(parents=True)
    payload = b"test executable"
    win = artifacts / "EMTGv9-windows-x64.exe"
    win.write_bytes(payload)
    with zipfile.ZipFile(artifacts / "EMTG-9.2.0-Windows-AMD64.zip", "w") as archive:
        archive.writestr("EMTG/bin/EMTGv9.exe", payload)
    provenance.write_receipt(root, provenance.source_identity(root), artifacts, win, "windows-x64",
                             {"ci": {"run_id": "fixture-run"}})
    linux = artifacts / "EMTGv9-linux-x64-experimental"
    linux.write_bytes(payload)
    with tarfile.open(artifacts / "EMTG-9.2.0-Linux-x86_64-experimental.tar.gz", "w:gz") as archive:
        item = tarfile.TarInfo("EMTG/bin/EMTGv9")
        item.size = len(payload)
        archive.addfile(item, io.BytesIO(payload))
    provenance.write_receipt(root, provenance.source_identity(root), artifacts, linux, "linux-x64-experimental",
                             {"ci": {"run_id": "fixture-run"}})
    return root, artifacts


@pytest.fixture
def gh_calls(publisher, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, 0, json.dumps({"isDraft": True}), "")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(publisher, "run_gh", run)
    return calls


def test_qualified_draft_has_verified_assets_checksums_and_remains_draft(bundles, publisher, gh_calls):
    root, artifacts = bundles
    publisher.prepare_draft(root, artifacts, "v9.2.0", "owner/repository", "fixture-run")
    upload = next(c for c in gh_calls if c[:2] == ["release", "upload"])
    assert str(artifacts / "SHA256SUMS") in upload
    assert str(artifacts / "EMTG-windows-x64.provenance.json") in upload
    assert str(artifacts / "EMTG-linux-x64-experimental.provenance.json") in upload
    assert "EMTGv9-windows-x64.exe" in (artifacts / "SHA256SUMS").read_text()
    assert "EMTGv9-linux-x64-experimental" in (artifacts / "SHA256SUMS").read_text()
    edit = next(c for c in gh_calls if c[:2] == ["release", "edit"])
    assert "--draft" in edit
    assert all("--draft=false" not in c for c in gh_calls)


@pytest.mark.parametrize("problem", ["missing-linux", "damaged-executable", "new-source", "unexpected-asset", "wrong-run", "wrong-tag"])
def test_unqualified_input_fails_before_any_github_action(bundles, publisher, gh_calls, problem):
    root, artifacts = bundles
    run, tag = "fixture-run", "v9.2.0"
    if problem == "missing-linux":
        (artifacts / "EMTG-linux-x64-experimental.provenance.json").unlink()
    elif problem == "damaged-executable":
        (artifacts / "EMTGv9-windows-x64.exe").write_bytes(b"changed binary")
    elif problem == "new-source":
        (root / "change.txt").write_text("new source")
        git(root, "add", ".")
        git(root, "commit", "-qm", "different source")
        git(root, "tag", "-f", "v9.2.0")
    elif problem == "unexpected-asset":
        (artifacts / "unlisted.txt").write_text("unreviewed content")
    elif problem == "wrong-run":
        run = "another-run"
    else:
        tag = "v9.3.0"
    with pytest.raises((ValueError, OSError)):
        publisher.prepare_draft(root, artifacts, tag, "owner/repository", run)
    assert not gh_calls


def test_published_release_cannot_be_overwritten(bundles, publisher, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({"isDraft": False}), "")
    monkeypatch.setattr(publisher, "run_gh", run)
    with pytest.raises(ValueError, match="Published"):
        publisher.prepare_draft(*bundles, "v9.2.0", "owner/repository")
    assert len(calls) == 1


def test_upload_failure_does_not_publish_or_announce_completion(bundles, publisher, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, 0, '{"isDraft": true}', "")
        return subprocess.CompletedProcess(args, 1, "", "upload failed")
    monkeypatch.setattr(publisher, "run_gh", run)
    with pytest.raises(RuntimeError, match="attach"):
        publisher.prepare_draft(*bundles, "v9.2.0", "owner/repository")
    assert not any(c[:2] == ["release", "edit"] for c in calls)


def test_missing_release_is_created_as_a_draft(bundles, publisher, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        if args[:2] == ["release", "view"]:
            return subprocess.CompletedProcess(args, 1, "", "release not found\n")
        return subprocess.CompletedProcess(args, 0, "", "")
    monkeypatch.setattr(publisher, "run_gh", run)
    publisher.prepare_draft(*bundles, "v9.2.0", "owner/repository")
    create = next(c for c in calls if c[:2] == ["release", "create"])
    assert "--draft" in create and "--verify-tag" in create


def test_github_read_failure_is_not_treated_as_a_missing_release(bundles, publisher, monkeypatch):
    calls = []
    def run(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "HTTP 502")
    monkeypatch.setattr(publisher, "run_gh", run)
    with pytest.raises(RuntimeError, match="inspect"):
        publisher.prepare_draft(*bundles, "v9.2.0", "owner/repository")
    assert len(calls) == 1
