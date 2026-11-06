"""read_credentials_dir() / client_from_env(credentials_dir=...) — login read from a private folder.

Offline: tmp files only, no robot or cloud involved.
"""

import logging
import os

from dreame_mcp.client import client_from_env, read_credentials_dir


def _folder(tmp_path, user="me@example.com", password="s3cret", mode=0o600):
    d = tmp_path / "dreame"
    d.mkdir()
    for name, value in (("user", user), ("password", password)):
        if value is not None:
            f = d / name
            f.write_text(value + "\n", encoding="utf-8")
            f.chmod(mode)
    return d


def test_reads_and_strips_both_files(tmp_path):
    assert read_credentials_dir(_folder(tmp_path)) == ("me@example.com", "s3cret")


def test_missing_file_gives_none(tmp_path):
    assert read_credentials_dir(_folder(tmp_path, password=None)) == ("me@example.com", None)


def test_expands_tilde(tmp_path, monkeypatch):
    _folder(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert read_credentials_dir("~/dreame") == ("me@example.com", "s3cret")


def test_warns_when_others_can_read(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="dreame-mcp.client"):
        read_credentials_dir(_folder(tmp_path, mode=0o644))
    assert "chmod 600" in caplog.text
    assert "s3cret" not in caplog.text


def test_folder_wins_over_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("DREAME_USER", "env@example.com")
    monkeypatch.setenv("DREAME_PASSWORD", "envpass")
    client = client_from_env(_folder(tmp_path))
    assert client._username == "me@example.com"
    assert client._password == "s3cret"


def test_environment_used_without_folder(monkeypatch):
    monkeypatch.setenv("DREAME_USER", "env@example.com")
    monkeypatch.setenv("DREAME_PASSWORD", "envpass")
    client = client_from_env()
    assert client._username == "env@example.com"
    assert os.environ["DREAME_PASSWORD"] == "envpass"
