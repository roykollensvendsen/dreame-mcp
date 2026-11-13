"""The voice and the web page wait for each other after a failed cloud login.

They are two processes with a client each, so the wait is kept in one small file
(dreame_mcp.login_wait); the conftest gives every test its own state folder. The fake
login copies the real one: a refused cloud login leaves a reason in ``cloud_error``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from dreame_mcp.voice import Link, Unavailable


class Login:
    """A client whose cloud login works or is refused, counting the tries."""

    def __init__(self, *, ok: bool = True):
        self.ok = ok
        self.connected = False
        self.cloud_error: str | None = None
        self.tries = 0

    async def connect(self) -> bool:
        self.tries += 1
        self.connected = self.ok
        self.cloud_error = None if self.ok else "cloud login failed: Xiaomi rejected the credentials (70016)"
        return self.ok

    def disconnect(self) -> None:
        pass


def _wait_file() -> Path:
    return Path(os.environ["XDG_STATE_HOME"]) / "dreame-mcp" / "last-failed-login"


def _leave(at: float) -> None:
    """What the other process leaves after a failed login: the time and a reason code."""
    _wait_file().parent.mkdir(parents=True, exist_ok=True)
    _wait_file().write_text(json.dumps({"at": at, "reason": "failed"}))
    os.utime(_wait_file(), (at, at))


async def test_a_failed_login_in_either_process_makes_the_other_wait():
    _leave(time.time() - 10)  # the web page's login failed ten seconds ago
    client = Login()
    with pytest.raises(Unavailable) as said:
        await Link(client).ready(time.monotonic() + 5)
    assert client.tries == 0
    assert "a moment ago" in said.value.say
    assert said.value.then


async def test_a_failed_voice_login_makes_the_page_wait():
    client = Login(ok=False)
    with pytest.raises(Unavailable):
        await Link(client).ready(time.monotonic() + 5)
    assert json.loads(_wait_file().read_text())["reason"] == "failed"


async def test_a_good_voice_login_ends_the_wait_for_the_page():
    _leave(time.time() - 120)  # an old failure whose wait is over
    client = Login()
    assert await Link(client).ready(time.monotonic() + 5) is client
    assert not _wait_file().exists()


async def test_after_the_shared_wait_the_voice_logs_in_again():
    _leave(time.time() - 61)
    client = Login()
    assert await Link(client).ready(time.monotonic() + 5) is client
    assert client.tries == 1


@pytest.fixture
def unwritable(monkeypatch, tmp_path):
    blocker = tmp_path / "blocked-state"
    blocker.write_text("not a folder")  # the state folder cannot be made
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker))


async def test_a_good_login_gets_through_when_the_wait_file_cannot_be_written(unwritable):
    client = Login()
    assert await Link(client).ready(time.monotonic() + 5) is client


async def test_a_failed_login_is_still_said_when_the_wait_file_cannot_be_written(unwritable):
    client = Login(ok=False)
    with pytest.raises(Unavailable) as said:
        await Link(client).ready(time.monotonic() + 5)
    assert "turned down the login" in said.value.say
