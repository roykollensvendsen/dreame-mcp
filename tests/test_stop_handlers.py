"""A test that sets up the scripts' stop handlers leaves none behind for the tests after it.

scripts/mutate.py's main() and the ratchet's stop_cleanly() handle SIGINT, SIGTERM and
SIGHUP, and once stopping, ignore all three. A test that calls them does so in pytest's
own process, so without tests/conftest.py putting the handlers back, every later test
would run with them: a Ctrl-C would no longer stop the suite the usual way, and a later
test relying on Python's own handling would fail, which the mutation run reports as a
rule "caught only by another test" (tv-mcp #84 found it).
"""

from __future__ import annotations

import importlib.util
import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

#: The handlers this pytest started with, read when this file is collected, before any test
#: runs. Not Python's defaults: under nohup, or a runner that ignores SIGHUP, a process
#: starts with it ignored, and that is no leak.
STARTED_WITH = {each: signal.getsignal(each) for each in STOPS}


def _load_mutate():
    spec = importlib.util.spec_from_file_location("mutate", ROOT / "scripts" / "mutate.py")
    assert spec and spec.loader
    mutate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mutate)
    return mutate


def test_sets_the_stop_handlers_as_a_run_does():
    # What mutate.main() does first, in this process, and leaves for the fixture to undo.
    _load_mutate().stop_cleanly()
    assert all(signal.getsignal(each) != STARTED_WITH[each] for each in STOPS)  # it did set them


def test_has_the_stop_handlers_this_pytest_started_with():
    assert {each: signal.getsignal(each) for each in STOPS} == STARTED_WITH


def test_a_test_that_sets_stop_handlers_leaves_none_behind(tmp_path):
    # Two tests of this file, in this order, in a pytest of their own: the first sets the
    # stop handlers as a run does (and checks they changed), the second checks what the
    # handlers are afterwards.
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            f"--basetemp={tmp_path / 'pytest'}",
            "tests/test_stop_handlers.py::test_sets_the_stop_handlers_as_a_run_does",
            "tests/test_stop_handlers.py::test_has_the_stop_handlers_this_pytest_started_with",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "2 passed" in done.stdout, done.stdout[-2000:]
