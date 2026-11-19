"""A ratchet that cannot measure its base must not pass the change.

On 19.11 a check with a base that was not a commit said "nothing to compare with" and
exited 0, so every level could have got worse unseen. Now a base that is not a commit,
or that has code but cannot be measured, fails the check. The one case that still passes is
a base with no code to measure yet (the first pull request of a new repository), and it says
so plainly.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("ratchet_base", ROOT / "scripts" / "ratchet.py")
assert _spec and _spec.loader
ratchet = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ratchet)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """A repository whose first commit has no code, and whose second adds the package."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "README.md").write_text("new\n")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "empty")
    _git(root, "tag", "empty")
    package = root / "src" / "dreame_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "code")
    monkeypatch.setattr(ratchet, "ROOT", root)
    monkeypatch.setattr(ratchet, "PACKAGE", root / "src" / "dreame_mcp")
    return root


def test_a_base_that_is_not_a_commit_fails_the_check(repo, monkeypatch):
    monkeypatch.setattr(ratchet, "measure_at", lambda ref: pytest.fail("measured a base that is not a commit"))
    with pytest.raises(ratchet.BaseUnreadable, match="is not a commit"):
        ratchet.base_for("/no/such/folder")


def test_a_base_with_code_that_cannot_be_measured_fails_the_check(repo, monkeypatch):
    monkeypatch.setattr(ratchet, "measure_at", lambda ref: None)
    with pytest.raises(ratchet.BaseUnreadable, match="could not be measured"):
        ratchet.base_for("main")


def test_a_base_with_no_code_yet_passes_and_says_so(repo, monkeypatch, capsys):
    monkeypatch.setattr(ratchet, "measure_at", lambda ref: pytest.fail("measured a base with no code"))
    assert ratchet.base_for("empty") is None
    assert "has no code to measure yet" in capsys.readouterr().out


def test_a_base_with_code_is_measured(repo, monkeypatch):
    monkeypatch.setattr(ratchet, "measure_at", lambda ref: {"tests": 1, "ref": ref})
    assert ratchet.base_for("main") == {"tests": 1, "ref": "main"}
