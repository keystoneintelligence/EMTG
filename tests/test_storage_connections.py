"""Database handles must be released on both successful and failed operations."""
import errno
import os
import sqlite3

import pytest

from OuterLoop.storage import _connection
from OuterLoop import storage


def test_connection_commits_rolls_back_and_releases_file(tmp_path):
    path = tmp_path / "cache.sqlite"
    with _connection(path) as connection:
        connection.execute("CREATE TABLE values_seen(value INTEGER)")
        connection.execute("INSERT INTO values_seen VALUES (1)")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")

    with pytest.raises(RuntimeError, match="abort"):
        with _connection(path) as failed:
            failed.execute("INSERT INTO values_seen VALUES (2)")
            raise RuntimeError("abort")
    with pytest.raises(sqlite3.ProgrammingError):
        failed.execute("SELECT 1")
    with _connection(path) as connection:
        assert [row[0] for row in connection.execute("SELECT value FROM values_seen")] == [1]
    # On Windows an unclosed connection prevents this operation.
    path.unlink()
    assert not path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows CRT delete-pending lock race")
def test_file_lock_retries_delete_pending_but_propagates_permanent_denial(tmp_path, monkeypatch):
    path = tmp_path / "cache.lock"
    original_open = storage.os.open
    attempts = []

    def delayed_open(*args, **kwargs):
        attempts.append(None)
        if len(attempts) <= 2:
            raise PermissionError(errno.EACCES, "Permission denied", str(path))
        return original_open(*args, **kwargs)

    monkeypatch.setattr(storage.os, "open", delayed_open)
    with storage.exclusive_file_lock(path):
        assert path.is_file()
        assert str(os.getpid()) in path.read_text()
    assert len(attempts) == 3
    assert not path.exists()

    def denied_open(*args, **kwargs):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))

    monkeypatch.setattr(storage.os, "open", denied_open)
    with pytest.raises(PermissionError):
        with storage.exclusive_file_lock(path, timeout_seconds=0):
            pytest.fail("A denied lock must never be acquired")
