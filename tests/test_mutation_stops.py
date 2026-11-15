"""A mutation run that is stopped still puts the source back and leaves nothing behind.

A real run of scripts/mutate.py, on a tiny project, in its own process: it is stopped with
Ctrl-C (SIGINT) while the suite runs, and then, while it is putting the mutated line back
(slowed down here so the moment can be hit), with SIGTERM, as an impatient person or a
cancelled CI job would. The second signal must not cut the restore short.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

ORIGINAL = "def answer():\n    return 42\n"

#: The suite waits until it is stopped; it says when it has started.
SLOW_TEST = """
    import os, time
    from pathlib import Path

    def test_answer():
        Path(os.environ["MARKS"], "suite-started").touch()
        time.sleep(120)
"""

#: Runs mutate.main() on the tiny project, with the restore of the source slowed down.
RUNNER = """
    import importlib.util, os, sys, time
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("mutate", sys.argv[1])
    mutate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutate)
    project, marks = Path(sys.argv[2]), Path(os.environ["MARKS"])
    mutate.ROOT = project
    mutate.TABLE = project / "mutations.toml"
    mutate.RECOVERY = project / "recovery.json"
    mutate.MUTATE_TMP = Path(sys.argv[3])
    mutate.rules = lambda: [
        {"name": "answer", "file": "answer.py", "find": "return 42", "replace": "return 0"}
    ]
    write = Path.write_text

    def slow_write(path, text, *args, **kwargs):
        if path.name == "answer.py" and text == {original!r}:
            (marks / "restore-began").touch()
            time.sleep(2)
        return write(path, text, *args, **kwargs)

    Path.write_text = slow_write
    sys.exit(mutate.main())
"""


def _wait_for(mark: Path, child: subprocess.Popen) -> None:
    for _ in range(600):
        if mark.exists():
            return
        assert child.poll() is None, child.communicate()
        time.sleep(0.05)
    raise AssertionError(f"{mark.name} never came")


def _start(tmp_path: Path) -> tuple[subprocess.Popen, Path, Path, Path]:
    project, marks, temp = tmp_path / "project", tmp_path / "marks", tmp_path / "mutate-tmp"
    (project / "tests").mkdir(parents=True)
    marks.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "answer.py").write_text(ORIGINAL)
    (project / "tests" / "test_answer.py").write_text(textwrap.dedent(SLOW_TEST))
    runner = tmp_path / "runner.py"
    runner.write_text(textwrap.dedent(RUNNER).replace("{original!r}", repr(ORIGINAL)))
    child = subprocess.Popen(
        [sys.executable, str(runner), str(ROOT / "scripts" / "mutate.py"), str(project), str(temp)],
        env={**os.environ, "MARKS": str(marks)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    _wait_for(marks / "suite-started", child)
    assert (project / "answer.py").read_text() != ORIGINAL  # mutated while the suite runs
    return child, project, marks, temp


def _cleaned_up(child: subprocess.Popen, project: Path, temp: Path) -> None:
    try:
        child.communicate(timeout=60)
    finally:
        if child.poll() is None:
            child.kill()
    assert (project / "answer.py").read_text() == ORIGINAL
    assert not (project / "recovery.json").exists()
    assert not temp.exists()


@pytest.mark.parametrize("stop", [signal.SIGTERM, signal.SIGHUP], ids=["SIGTERM", "SIGHUP"])
def test_a_stopped_run_still_cleans_up(tmp_path, stop):
    child, project, _, temp = _start(tmp_path)
    child.send_signal(stop)
    _cleaned_up(child, project, temp)
    assert child.returncode == 128 + stop  # stopped the way a shell reports it, not killed


def test_a_second_stop_does_not_cut_the_cleanup_short(tmp_path):
    child, project, marks, temp = _start(tmp_path)
    child.send_signal(signal.SIGINT)
    _wait_for(marks / "restore-began", child)
    child.send_signal(signal.SIGTERM)
    _cleaned_up(child, project, temp)
    assert child.returncode != 0
