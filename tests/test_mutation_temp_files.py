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
import os
import stat
import subprocess
import sys
import textwrap
import time
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
    mutate.MUTATE_TMP.mkdir(mode=0o700)  # as a run makes it
    left = mutate.MUTATE_TMP / "run-killed"
    (left / "locked").mkdir(parents=True)
    (left / "out").symlink_to(project, target_is_directory=True)
    (left / "locked").chmod(0)
    monkeypatch.setattr(mutate, "rules", lambda: [])
    monkeypatch.setattr(mutate, "RECOVERY", mutate.MUTATE_TMP / "no-recovery.json")
    assert mutate.main() == 0
    assert not mutate.MUTATE_TMP.exists()  # the leftover went, and with it the emptied folder
    assert (project / "theirs").is_dir()


def test_a_finished_run_leaves_not_even_its_empty_folder(project, monkeypatch, capsys):
    # The ratchet measures its base in a copy at a new path each time, so a folder per
    # checkout kept after the run would add an empty one to /tmp with every ratchet run.
    rule = {"name": "fails", "file": "tests/test_mess.py", "find": r"assert False", "replace": "assert 1 == 2"}
    monkeypatch.setattr(mutate, "rules", lambda: [rule])  # one real suite run, its test caught
    monkeypatch.setattr(mutate, "RECOVERY", project / "no-recovery.json")
    assert mutate.main() == 0
    assert "1/1 rules failed" in capsys.readouterr().out
    assert not mutate.MUTATE_TMP.exists()


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


def test_a_suite_run_has_no_time_limit(project, monkeypatch):
    # Roy's rule: mutation runs are never time-limited, since a suite cut short proves
    # nothing. A mutation that makes the tests hang therefore hangs the run, in plain sight.
    seen: dict = {}

    def run(*args, **kwargs):
        seen.update(kwargs)
        return mutate.subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(mutate.subprocess, "run", run)
    assert mutate.failing_tests() == []
    assert "timeout" not in seen


#: Another run of this checkout, still going: it holds its folder's lock until stopped.
HOLDS_A_RUN = """
import fcntl, os, sys, time
fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
fcntl.flock(fd, fcntl.LOCK_EX)
open(sys.argv[2], "w").close()
time.sleep(120)
"""


def test_a_sweep_leaves_the_folder_of_a_run_still_going_alone(project, tmp_path):
    mutate.MUTATE_TMP.mkdir(mode=0o700)  # as a run makes it
    going, ready = mutate.MUTATE_TMP / "run-going", tmp_path / "ready"
    (going / "pytest").mkdir(parents=True)
    other = subprocess.Popen([sys.executable, "-c", HOLDS_A_RUN, str(going), str(ready)])
    try:
        for _ in range(600):
            if ready.exists():
                break
            time.sleep(0.05)
        mutate.sweep()
        assert (going / "pytest").is_dir()  # its run is still going
    finally:
        other.kill()
        other.wait()
    mutate.sweep()
    assert not going.exists()  # its run is gone, so the folder is a leftover


def test_a_run_folder_is_locked_before_anyone_can_see_it(project, monkeypatch):
    # A sweep holds MUTATE_TMP's lock while it looks, so a folder made under that lock and
    # locked before it is released is never seen unlocked.
    seen = []
    real = mutate.tempfile.mkdtemp

    def mkdtemp(*args, **kwargs):
        seen.append(mutate.held(mutate.MUTATE_TMP))  # MUTATE_TMP itself is locked meanwhile
        return real(*args, **kwargs)

    monkeypatch.setattr(mutate.tempfile, "mkdtemp", mkdtemp)
    run, lock = mutate.new_run()
    try:
        assert seen == [True]
        assert mutate.held(run)
    finally:
        os.close(lock)
    assert not mutate.held(run)
    mutate.remove(run)


def test_a_run_trusts_its_folder_only_when_it_is_really_ours(project, tmp_path, monkeypatch):
    # MUTATE_TMP has a name anyone can guess in a /tmp everyone shares. Whatever is planted
    # there first must stop the run, not steer what the sweep removes.
    target = tmp_path / "someone-elses"
    (target / "precious").mkdir(parents=True)
    mutate.MUTATE_TMP.symlink_to(target, target_is_directory=True)
    with pytest.raises(SystemExit, match="is not a folder of yours"):
        mutate.sweep()
    assert (target / "precious").is_dir()  # nothing removed through the link
    mutate.MUTATE_TMP.unlink()

    mutate.MUTATE_TMP.mkdir(mode=0o755)  # others may enter, so others may have put things in it
    mutate.MUTATE_TMP.chmod(0o755)
    with pytest.raises(SystemExit, match="others may"):
        mutate.sweep()
    mutate.MUTATE_TMP.chmod(0o700)

    other = os.getuid() + 1
    monkeypatch.setattr(mutate.os, "getuid", lambda: other)  # as if made by another
    with pytest.raises(SystemExit, match="is not a folder of yours"):
        mutate.sweep()
    monkeypatch.undo()
    mutate.sweep()  # really ours: fine


def test_a_sweep_removes_what_no_run_can_hold(project):
    # A file, a link and a folder no one may open are no run's: they go, the link without
    # its target.
    mutate.MUTATE_TMP.mkdir(mode=0o700)
    (mutate.MUTATE_TMP / "stray-file").write_text("x")
    (mutate.MUTATE_TMP / "link").symlink_to(project, target_is_directory=True)
    (mutate.MUTATE_TMP / "closed").mkdir()
    (mutate.MUTATE_TMP / "closed").chmod(0)
    mutate.sweep()
    assert list(mutate.MUTATE_TMP.iterdir()) == []
    assert (project / "theirs").is_dir()
