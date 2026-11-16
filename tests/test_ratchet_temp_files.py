"""The quality ratchet and the mutation run leave nothing in the temporary folder.

The real limit: on 2026-11-14 /tmp ran out of inodes, and the next day 579 folders
(3.1 GB) that test, mutation and ratchet runs had left were deleted by hand. Here the
temporary folder is the test's own, and the ratchet's suite runs a tiny project with
real pytest, whose test leaves a folder with no permissions, as tests do.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ratchet = _load("ratchet")
mutate = _load("mutate")

LEAVES_A_LOCKED_FOLDER = """
    import pytest

    import dreame_mcp  # something for --cov=dreame_mcp to measure

    @pytest.fixture(scope="session")
    def shared(tmp_path_factory):
        return tmp_path_factory.mktemp("shared")

    def test_passes(tmp_path, shared):
        (tmp_path / "locked" / "deeper").mkdir(parents=True)
        (tmp_path / "locked").chmod(0)
        (shared / "kept").write_text("x")
"""


@pytest.fixture
def temp(tmp_path, monkeypatch):
    """The temporary folder every process here uses, empty to start with."""
    folder = tmp_path / "tmp"
    folder.mkdir()
    monkeypatch.setenv("TMPDIR", str(folder))
    monkeypatch.setattr(tempfile, "tempdir", str(folder))
    return folder


def test_a_ratchet_suite_run_leaves_no_temporary_files_behind(tmp_path, temp, monkeypatch):
    project = tmp_path / "project"
    (project / "tests").mkdir(parents=True)
    (project / "pytest.ini").write_text("[pytest]\npythonpath = .\n")
    (project / "dreame_mcp").mkdir()
    (project / "dreame_mcp" / "__init__.py").write_text("ONE = 1\n")
    (project / "tests" / "test_one.py").write_text(textwrap.dedent(LEAVES_A_LOCKED_FOLDER))
    monkeypatch.setattr(ratchet, "ROOT", project)
    monkeypatch.setattr(ratchet, "PYTEST", (sys.executable, "-m", "pytest"))
    suite, _, _ = ratchet._suite()
    assert "1 passed" in suite.stdout  # it really ran
    left = list(temp.iterdir())
    _open_all(temp)  # so this test's own folder can go, whatever it found
    assert left == []


def _open_all(folder: Path) -> None:
    for top, dirs, _ in os.walk(folder):
        for name in dirs:
            (Path(top) / name).chmod(0o700)


@pytest.mark.parametrize("script", [ratchet, mutate], ids=["ratchet", "mutate"])
def test_any_stop_ends_a_run_the_way_ctrl_c_does(script):
    # A stop (Ctrl-C, kill, a cancelled CI job, a closed terminal) runs the finally blocks;
    # once stopping, the others are ignored, so a second one cannot cut them short.
    stops = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
    before = {each: signal.getsignal(each) for each in stops}
    try:
        script.stop_cleanly()
        with pytest.raises(SystemExit) as stopped:
            os.kill(os.getpid(), signal.SIGTERM)  # Python runs the handler straight after
        assert stopped.value.code == 128 + signal.SIGTERM
        assert all(signal.getsignal(each) == signal.SIG_IGN for each in stops)
        script.stop_cleanly()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGINT)
    finally:
        for each, handler in before.items():
            signal.signal(each, handler)


#: Stands in for the ratchet: runs a command through its _run, and can be stopped.
RATCHET = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("ratchet", sys.argv[1])
ratchet = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ratchet)
ratchet.stop_cleanly()
ratchet._run(*sys.argv[3:], "-c", sys.argv[2])
"""

#: How the stand-in starts its child: straight, or through uv, as the ratchet starts its own.
STRAIGHT = (sys.executable,)
THROUGH_UV = ("uv", "run", "--no-project", "python")

#: Stands in for the mutation run: says it is going, and on Ctrl-C puts its line back. Every
#: Ctrl-C it gets is counted: a second one would make pytest cut its own cleanup short.
CHILD = """
import pathlib, signal, sys, time
marks = pathlib.Path({marks!r})
def put_back(*_):
    with open(marks / "put-back", "a") as told:
        told.write("x")
    time.sleep(1)  # room for a second Ctrl-C to arrive and be counted
    sys.exit(130)
signal.signal(signal.SIGINT, put_back)
(marks / "going").touch()
time.sleep(120)
"""


def _stop_while_child_runs(tmp_path, stop, starts=STRAIGHT):
    marks = tmp_path / "marks"
    marks.mkdir()
    child = CHILD.replace("{marks!r}", repr(str(marks)))
    stand_in = subprocess.Popen(
        [sys.executable, "-c", RATCHET, str(ROOT / "scripts" / "ratchet.py"), child, *starts],
        start_new_session=True,  # its own process group, as a terminal gives a command
    )
    try:
        for _ in range(600):
            if (marks / "going").exists():
                break
            time.sleep(0.05)
        stop(stand_in)
        code = stand_in.wait(timeout=60)
    finally:
        if stand_in.poll() is None:
            os.killpg(stand_in.pid, signal.SIGKILL)
    return code, marks


def test_a_stopped_ratchet_lets_its_mutation_run_put_its_line_back(tmp_path):
    code, marks = _stop_while_child_runs(tmp_path, lambda p: p.send_signal(signal.SIGTERM))  # kill, CI
    assert code == 128 + signal.SIGTERM
    assert (marks / "put-back").read_text() == "x"  # told with Ctrl-C, not killed outright


def test_ctrl_c_in_a_terminal_reaches_the_ratchets_child_once(tmp_path):
    # A terminal's Ctrl-C goes to the whole process group: ratchet and child alike.
    code, marks = _stop_while_child_runs(tmp_path, lambda p: os.killpg(p.pid, signal.SIGINT))
    assert code != 0
    assert (marks / "put-back").read_text() == "x"  # once, not twice


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv, which the ratchet starts its runs through")
@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGINT], ids=["kill", "ctrl-c"])
def test_a_stopped_ratchet_tells_the_python_under_uv_once(tmp_path, stop):
    # The ratchet starts its runs through uv, and uv passes a Ctrl-C on to its own child.
    # Sent to uv's whole process group instead, it would reach that child twice.
    def send(stand_in):
        if stop == signal.SIGINT:
            os.killpg(stand_in.pid, stop)  # a terminal's Ctrl-C, to the ratchet's group
        else:
            stand_in.send_signal(stop)

    code, marks = _stop_while_child_runs(tmp_path, send, THROUGH_UV)
    assert code != 0
    assert (marks / "put-back").read_text() == "x"  # once, not twice


#: Stands in for a suite the ratchet runs: says it is going, waits until the ratchet is gone,
#: then writes a lot, as pytest's summary does, and only then marks itself finished.
OUTLIVES = """
import os, pathlib, sys, time
marks = pathlib.Path({marks!r})
(marks / "going").write_text(str(os.getpid()))
while not (marks / "ratchet-gone").exists():
    time.sleep(0.05)
print("x" * 1_000_000, flush=True)
print("y" * 1_000_000, file=sys.stderr, flush=True)
(marks / "finished").touch()
"""


def test_a_child_outlives_a_ratchet_killed_with_kill_9_and_leaves_no_file(tmp_path, temp):
    # kill -9 is the way out the ratchet names when its child hangs; the child must then
    # still finish (a mutation run puts its line back), not die writing into a dead pipe.
    marks = tmp_path / "marks"
    marks.mkdir()
    child = OUTLIVES.replace("{marks!r}", repr(str(marks)))
    stand_in = subprocess.Popen([sys.executable, "-c", RATCHET, str(ROOT / "scripts" / "ratchet.py"), child, *STRAIGHT])
    pid = None
    try:
        for _ in range(600):
            if (marks / "going").exists() and (marks / "going").read_text():
                pid = int((marks / "going").read_text())
                break
            time.sleep(0.05)
        assert pid, "the child never started"
        stand_in.kill()
        stand_in.wait(timeout=60)
        (marks / "ratchet-gone").touch()
        for _ in range(600):
            if (marks / "finished").exists() or not Path(f"/proc/{pid}").exists():
                break
            time.sleep(0.05)
        assert (marks / "finished").exists(), "the child died once the ratchet was gone"
        assert sorted(p.name for p in temp.iterdir()) == []
    finally:
        if stand_in.poll() is None:
            stand_in.kill()
        if pid and Path(f"/proc/{pid}").exists():
            os.kill(pid, signal.SIGKILL)
