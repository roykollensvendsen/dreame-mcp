"""A test that sets up the scripts' stop handlers leaves none behind for the tests after it.

scripts/mutate.py's main() and the ratchet's stop_cleanly() handle SIGINT, SIGTERM and
SIGHUP, and once stopping, ignore all three. A test that calls them does so in pytest's
own process, so without tests/conftest.py putting the handlers back, every later test
would run with them: a Ctrl-C would no longer stop the suite the usual way, and a later
test relying on Python's own handling would fail, which the mutation run reports as a
rule "caught only by another test" (tv-mcp #84 found it).
"""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STOPS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def test_starts_with_pythons_own_stop_handlers():
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert signal.getsignal(signal.SIGTERM) == signal.SIG_DFL
    assert signal.getsignal(signal.SIGHUP) == signal.SIG_DFL


def test_a_test_that_sets_stop_handlers_leaves_none_behind(tmp_path):
    # Two real tests of this suite, in this order, in a pytest of their own: the first runs
    # mutate.main() in-process, the second checks what the handlers are afterwards.
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
            "tests/test_mutation_temp_files.py::test_a_finished_run_leaves_not_even_its_empty_folder",
            "tests/test_stop_handlers.py::test_starts_with_pythons_own_stop_handlers",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert "2 passed" in done.stdout, done.stdout[-2000:]
