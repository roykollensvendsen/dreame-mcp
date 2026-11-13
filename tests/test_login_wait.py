"""The wait after a failed cloud login, kept in one file the voice and the web page share.

The real limit: the Dreame cloud counts failed logins and then asks for a captcha (87001),
and the voice server and the web server are two processes, each with its own client. So
the wait lives in a file both read. A clock and a temporary folder stand in for the real
ones; no cloud is reached.
"""

from __future__ import annotations

import json
import os
import stat
import time

import pytest

from dreame_mcp.login_wait import LONGEST, LoginWait, default_path


class Clock:
    def __init__(self) -> None:
        # A second behind real time: the file is stamped with this clock's own time
        # (never ahead of the real one), and is not "from the future" either.
        self.now = time.time() - 1

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / "last-failed-login"


def test_no_file_is_no_wait(path, clock):
    assert LoginWait(path, clock=clock).left() == 0


def test_a_failed_login_waits_a_minute(path, clock):
    wait = LoginWait(path, clock=clock)
    wait.failed()
    clock.now += 5
    assert wait.left() == 55


def test_the_file_is_stamped_with_the_time_it_records(path, clock):
    LoginWait(path, clock=clock).failed()
    assert path.stat().st_mtime == json.loads(path.read_text())["at"]


def test_the_wait_ends_after_its_time(path, clock):
    wait = LoginWait(path, clock=clock)
    wait.failed()
    clock.now += 61
    assert wait.left() == 0


def test_a_failed_login_in_one_process_makes_the_other_wait(path, clock):
    LoginWait(path, clock=clock).failed()  # the web page, say
    assert LoginWait(path, clock=clock).left() > 0  # the voice


def test_the_file_holds_only_the_time_and_a_reason_code(path, clock):
    LoginWait(path, clock=clock).failed()
    assert json.loads(path.read_text()) == {"at": clock.now, "reason": "failed"}


def test_an_unknown_reason_is_kept_as_failed(path, clock):
    LoginWait(path, clock=clock).failed("the cloud said: wrong password for someone@example.com")
    assert json.loads(path.read_text())["reason"] == "failed"


def test_the_wait_file_is_private(path, clock):
    path.parent.mkdir(parents=True)
    own = path.with_name(f"{path.name}.{os.getpid()}.new")  # this writer's own leftover
    own.write_text("left over")
    os.chmod(own, 0o644)
    LoginWait(path, clock=clock).failed()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not own.exists()


def test_two_writers_at_once_both_land(path, clock):
    # The voice and the web page fail at the same moment: the other one is half-way
    # through writing its own new file. Neither write may be lost or break.
    path.parent.mkdir(parents=True)
    other = path.with_name(path.name + ".new")
    other.write_text("the other process, mid-write")
    other_pid = path.with_name(f"{path.name}.{os.getpid() + 1}.new")
    other_pid.write_text("another writer, mid-write")
    LoginWait(path, clock=clock).failed()
    assert json.loads(path.read_text())["reason"] == "failed"
    assert other.read_text() == "the other process, mid-write"
    assert other_pid.read_text() == "another writer, mid-write"


@pytest.fixture
def unwritable(tmp_path):
    """A state folder that cannot be made: a plain file stands where the folder should be."""
    blocker = tmp_path / "state"
    blocker.write_text("not a folder")
    return blocker / "dreame-mcp" / "last-failed-login"


def test_writing_the_wait_file_never_stops_a_login(unwritable, clock, caplog):
    wait = LoginWait(unwritable, clock=clock)
    wait.failed()  # a full disk, a quota, a folder that can't be written: no raise
    wait.succeeded()
    assert wait.left() == 0
    assert str(unwritable) in caplog.text


def test_a_successful_login_ends_the_wait(path, clock):
    wait = LoginWait(path, clock=clock)
    wait.failed()
    wait.succeeded()
    assert not path.exists()
    assert wait.left() == 0


def test_a_success_with_no_file_is_fine(path, clock):
    LoginWait(path, clock=clock).succeeded()
    assert not path.exists()


def test_a_kept_wait_ends_at_most_the_longest_wait_after_the_file_was_written(path, clock):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": clock.now + 10_000, "reason": "failed"}))
    os.utime(path, (clock.now, clock.now))
    assert LoginWait(path, clock=clock).left() == LONGEST


def test_a_wait_file_from_the_future_counts_as_no_wait(path, clock):
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"at": clock.now, "reason": "failed"}))
    os.utime(path, (clock.now + 3600, clock.now + 3600))
    assert LoginWait(path, clock=clock).left() == 0


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{",
        "[]",
        '"failed"',
        '{"at": 1}',
        '{"reason": "failed"}',
        '{"at": "soon", "reason": "failed"}',
        '{"at": null, "reason": "failed"}',
        '{"at": 1, "reason": "unheard of"}',
    ],
)
def test_a_damaged_wait_file_counts_as_no_wait(path, clock, text):
    path.parent.mkdir(parents=True)
    path.write_text(text)
    assert LoginWait(path, clock=clock).left() == 0


def test_the_file_lives_in_the_users_state_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    assert default_path() == tmp_path / "dreame-mcp" / "last-failed-login"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert default_path().parts[-4:] == (".local", "state", "dreame-mcp", "last-failed-login")


def test_the_folder_is_made_private_when_it_is_missing(path, clock):
    LoginWait(path, clock=clock).failed()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_writer_whose_clock_runs_ahead_still_makes_the_other_wait(path):
    # The voice's clock is wall time at start plus monotonic time since; after the wall
    # clock is stepped back (NTP after a suspend) it runs ahead of the real one.
    ahead = LoginWait(path, clock=lambda: time.time() + 3600)
    ahead.failed()
    assert LoginWait(path).left() > 0  # the web page, on the real clock
