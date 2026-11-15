"""The quality ratchet and the mutation run leave nothing in the temporary folder.

The real limit: on 2026-11-14 /tmp ran out of inodes, and the next day 579 folders
(3.1 GB) that test, mutation and ratchet runs had left were deleted by hand. Here the
temporary folder is the test's own, and the ratchet's suite runs a tiny project with
real pytest, whose test leaves a folder with no permissions, as tests do.
"""

from __future__ import annotations

import importlib.util
import os
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
ratchet._run(sys.executable, "-c", sys.argv[2])
"""

#: Stands in for the mutation run: says it is going, and on Ctrl-C puts its line back.
CHILD = """
import pathlib, signal, sys, time
marks = pathlib.Path({marks!r})
def put_back(*_):
    (marks / "put-back").touch()
    sys.exit(130)
signal.signal(signal.SIGINT, put_back)
(marks / "going").touch()
time.sleep(120)
"""


def test_a_stopped_ratchet_lets_its_mutation_run_put_its_line_back(tmp_path):
    marks = tmp_path / "marks"
    marks.mkdir()
    child = CHILD.replace("{marks!r}", repr(str(marks)))
    stand_in = subprocess.Popen([sys.executable, "-c", RATCHET, str(ROOT / "scripts" / "ratchet.py"), child])
    try:
        for _ in range(600):
            if (marks / "going").exists():
                break
            time.sleep(0.05)
        stand_in.send_signal(signal.SIGTERM)  # the ratchet alone, as kill or CI would
        assert stand_in.wait(timeout=60) == 128 + signal.SIGTERM
    finally:
        if stand_in.poll() is None:
            stand_in.kill()
    assert (marks / "put-back").exists()  # it was told with Ctrl-C, not killed outright
