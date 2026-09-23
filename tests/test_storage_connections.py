"""Database handles must be released on both successful and failed operations."""
import sqlite3

import pytest

from OuterLoop.storage import _connection


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
