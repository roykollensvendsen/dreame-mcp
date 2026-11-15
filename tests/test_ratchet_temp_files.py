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
import sys
import tempfile
import textwrap
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
def test_a_stopped_run_cleans_up_like_an_interrupted_one(script):
    # A run stopped with SIGTERM (a CI job cancelled, kill) runs its finally blocks and
    # removes its temporary folders, as Ctrl-C already did.
    before = signal.getsignal(signal.SIGTERM)
    try:
        script.stop_cleanly()
        with pytest.raises(SystemExit) as stopped:
            os.kill(os.getpid(), signal.SIGTERM)  # Python runs the handler straight after
        assert stopped.value.code == 128 + signal.SIGTERM
    finally:
        signal.signal(signal.SIGTERM, before)
