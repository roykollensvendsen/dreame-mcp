"""The mutation run and pytest leave no temporary files behind.

The real limit: on 2026-11-14 /tmp ran out of inodes, about 756k of its 1M taken by
the temporary folders test and mutation runs had kept. A test may leave a file or a
folder with no permissions, or a link to a folder outside; each suite run of
scripts/mutate.py gets its own folder, removed when the run ends, and a folder left by
a killed run goes at the next start. The suite run here is a real pytest run of a tiny
project, not a fake.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts" / "mutate.py")
assert _spec and _spec.loader
mutate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mutate)

#: A test that leaves what /tmp filled with: a file and a folder with no permissions, and
#: a link to a folder outside, which must not be touched.
LEAVES_A_MESS = """
    import os
    from pathlib import Path

    OUTSIDE = Path(os.environ["OUTSIDE"])

    def test_leaves_files_no_one_may_open(tmp_path):
        locked = tmp_path / "locked"
        (locked / "deeper").mkdir(parents=True)
        (locked / "deeper" / "file").write_text("x")
        (locked / "file").write_text("x")
        (locked / "file").chmod(0)
        (locked / "deeper").chmod(0)
        locked.chmod(0)
        (tmp_path / "out").symlink_to(OUTSIDE, target_is_directory=True)

    def test_fails(tmp_path):
        (tmp_path / "kept").write_text("x")
        assert False
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A tiny pytest project for mutate.py to run, its own MUTATE_TMP, and a folder outside."""
    root = tmp_path / "project"
    (root / "tests").mkdir(parents=True)
    (root / "pytest.ini").write_text("[pytest]\n")
    (root / "tests" / "test_mess.py").write_text(textwrap.dedent(LEAVES_A_MESS))
    outside = tmp_path / "outside"
    (outside / "theirs").mkdir(parents=True)
    outside.chmod(0o755)
    monkeypatch.setattr(mutate, "ROOT", root)
    monkeypatch.setattr(mutate, "TABLE", root / "mutations.toml")
    monkeypatch.setattr(mutate, "MUTATE_TMP", tmp_path / "mutate-tmp")
    monkeypatch.setenv("OUTSIDE", str(outside))
    return outside


def test_a_suite_run_leaves_no_temporary_files_behind(project):
    assert mutate.failing_tests() == ["test_fails"]  # it really ran
    assert list(mutate.MUTATE_TMP.iterdir()) == []
    # The link was removed, not followed: the folder outside keeps its files and its mode.
    assert (project / "theirs").is_dir()
    assert stat.S_IMODE(project.stat().st_mode) == 0o755


def test_temporary_files_a_killed_run_left_are_swept_at_the_next_start(project, monkeypatch, capsys):
    left = mutate.MUTATE_TMP / "run-killed"
    (left / "locked").mkdir(parents=True)
    (left / "out").symlink_to(project, target_is_directory=True)
    (left / "locked").chmod(0)
    monkeypatch.setattr(mutate, "rules", lambda: [])
    monkeypatch.setattr(mutate, "RECOVERY", mutate.MUTATE_TMP / "no-recovery.json")
    assert mutate.main() == 0
    assert list(mutate.MUTATE_TMP.iterdir()) == []
    assert (project / "theirs").is_dir()


def test_the_runs_temporary_files_are_kept_outside_the_checkout():
    # Inside, a test that makes its own git repository would find this checkout around it.
    assert not mutate.MUTATE_TMP.is_relative_to(mutate.ROOT)
    assert mutate.MUTATE_TMP.name.startswith("dreame-mcp-mutate-")


def test_pytest_keeps_temporary_files_only_from_the_last_failed_run():
    options = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pytest"]["ini_options"]
    assert options["tmp_path_retention_count"] == 1
    assert options["tmp_path_retention_policy"] == "failed"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_removing_a_missing_folder_is_fine(tmp_path):
    mutate.remove(tmp_path / "never-made")
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    mutate.remove(link)
    assert not link.exists() and tmp_path.is_dir()
