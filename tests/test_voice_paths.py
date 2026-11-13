"""The voice server's less travelled paths: a read that fails after a command, odd values, start-up.

Offline: a scripted fake client, never the real robot or cloud.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from dreame_mcp import voice
from dreame_mcp.client import DreameStatus, _classify_cloud_failure
from dreame_mcp.voice import Link, Reading, Vacuum, _login_problem

DOCKED = {"2.1": 13, "3.1": 90, "3.2": 3}
IDLE_OFF_DOCK = {"2.1": 2, "3.1": 70, "3.2": 2}
CLEANING = {"2.1": 1, "3.1": 80, "3.2": 2}
FAILED = DreameStatus(error="no answer")
ROOMS = [{"id": 1, "name": "Kitchen"}, {"id": 2, "name": "Hall"}]


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Scripted:
    """A client whose status reads follow a script: each read takes the next, the last repeats."""

    def __init__(self, *reads: dict | DreameStatus, rooms=ROOMS, robot_room=None, docked=False):
        self.reads = [r if isinstance(r, DreameStatus) else DreameStatus(raw=r) for r in reads]
        self.connected = False
        self.cloud_error: str | None = None
        self.sent: list[str] = []
        self.connect_raises = False
        self.disconnect_raises = False
        self.logins = 0
        self.map_answer: dict[str, Any] = {
            "success": True,
            "source": "live",
            "map_data": {"room_list": rooms, "robot_room": robot_room, "docked": docked},
        }

    async def connect(self) -> bool:
        self.logins += 1
        if self.connect_raises:
            raise RuntimeError("socket closed")
        self.connected = True
        return True

    async def get_status(self) -> DreameStatus:
        return self.reads.pop(0) if len(self.reads) > 1 else self.reads[0]

    async def control(self, cmd: str) -> dict:
        self.sent.append(cmd)
        return {"success": True}

    async def get_map(self) -> dict:
        return self.map_answer

    async def clean_rooms(self, room_ids: list[int]) -> dict:
        self.sent.append(f"clean_rooms:{room_ids}")
        return {"success": True}

    def disconnect(self) -> None:
        if self.disconnect_raises:
            raise RuntimeError("already gone")
        self.connected = False


def _vacuum(client, clock=None) -> Vacuum:
    clock = clock or Clock()
    return Vacuum(Link(client, clock=clock), settle=0, clock=clock)


# -- reading odd values ------------------------------------------------------


def test_a_value_that_is_not_a_number_reads_as_zero():
    r = Reading.of(DreameStatus(raw={"2.1": "busy", "3.1": [1]}))
    assert (r.code, r.battery) == (0, 0)


def test_idle_and_fully_charged_is_said_as_on_the_dock():
    r = Reading.of(DreameStatus(raw={"2.1": 2, "3.1": 100, "3.2": 3}))
    assert r.state == "docked"
    assert r.words() == "on the dock, fully charged, battery 100 percent"


def test_a_captcha_is_said_as_an_extra_check():
    assert _login_problem("cloud asks for a captcha")[0].startswith("The Dreame cloud wants an extra check")


def test_a_rate_limited_login_is_said_as_too_many_logins():
    wrong, then = _login_problem(_classify_cloud_failure(SimpleNamespace(last_code="87001")))
    assert wrong == "The Dreame cloud has had too many logins and wants me to wait."
    assert then == "Ask again in a few minutes, not sooner."


# -- logging in and out ------------------------------------------------------


async def test_a_login_that_raises_is_said_as_unreachable():
    fake = Scripted(DOCKED)
    fake.connect_raises = True
    a = await _vacuum(fake).status()
    assert not a["success"]
    assert a["say"] == "I can't reach the Dreame cloud right now. Try again in a minute."


async def test_a_failed_read_logs_in_again_once_the_wait_is_over():
    clock = Clock()
    fake = Scripted(FAILED, DOCKED)
    v = _vacuum(fake, clock)
    first = await v.status()
    assert not first["success"]
    assert first["say"] == "I can't reach the Dreame cloud right now. Try again in a minute."
    clock.now += 61
    assert (await v.status())["success"]
    assert fake.logins == 2


async def test_closing_survives_a_disconnect_that_raises():
    fake = Scripted(DOCKED)
    fake.disconnect_raises = True
    v = _vacuum(fake)
    await v.status()
    v.link.close()  # no exception


# -- a command sent, then the read after it ------------------------------------


async def test_a_start_whose_read_back_fails_says_it_was_sent():
    fake = Scripted(IDLE_OFF_DOCK, FAILED)
    a = await _vacuum(fake).start()
    assert a == {"success": True, "say": "I sent the start. I couldn't check yet whether it set off."}
    assert fake.sent == ["start_clean"]


async def test_a_start_that_did_not_set_off_says_what_the_vacuum_says():
    fake = Scripted(IDLE_OFF_DOCK)
    a = await _vacuum(fake).start()
    assert a["say"] == "I sent the start. The vacuum says it is idle, battery 70 percent."


async def test_going_home_whose_read_back_fails_says_it_was_sent():
    fake = Scripted(IDLE_OFF_DOCK, FAILED)
    a = await _vacuum(fake).go_home()
    assert a == {"success": True, "say": "I sent it home. I couldn't check yet whether it set off."}


async def test_a_pause_whose_read_back_fails_says_so():
    fake = Scripted(CLEANING, FAILED)
    a = await _vacuum(fake).pause()
    assert a == {"success": True, "say": "Paused. I couldn't check the state afterwards."}


async def test_a_room_clean_whose_read_back_fails_says_it_was_sent():
    fake = Scripted(IDLE_OFF_DOCK, FAILED)
    a = await _vacuum(fake).clean_rooms(["hall"])
    assert a == {
        "success": True,
        "say": "I sent it to clean Hall. I couldn't check yet whether it set off.",
        "rooms": ["Hall"],
    }


async def test_a_room_clean_without_a_battery_reading_leaves_the_battery_out():
    fake = Scripted(IDLE_OFF_DOCK, {"2.1": 1})
    a = await _vacuum(fake).clean_rooms(["Kitchen"])
    assert a["say"] == "Started cleaning Kitchen."


async def test_a_room_clean_that_did_not_set_off_says_what_the_vacuum_says():
    fake = Scripted(IDLE_OFF_DOCK)
    a = await _vacuum(fake).clean_rooms(["Kitchen"])
    assert a["say"] == "I sent it to clean Kitchen. The vacuum says it is idle, battery 70 percent."


# -- where it is, and its rooms ----------------------------------------------


async def test_where_on_the_dock_while_charging():
    fake = Scripted({"2.1": 6, "3.1": 40, "3.2": 1}, robot_room=ROOMS[0], docked=True)
    a = await _vacuum(fake).where()
    assert a["say"] == "It's on its dock in Kitchen, charging, battery 40 percent."


async def test_where_on_the_map_but_outside_every_room():
    fake = Scripted(IDLE_OFF_DOCK)
    a = await _vacuum(fake).where()
    assert a["say"] == "It's on its map but not inside any room. The vacuum is idle, battery 70 percent."
    assert a["room"] is None


async def test_rooms_on_a_map_without_rooms():
    fake = Scripted(DOCKED, rooms=[])
    a = await _vacuum(fake).rooms()
    assert a == {"success": True, "say": "Its map has no rooms yet.", "rooms": []}


# -- starting the server -------------------------------------------------------


class _Started:
    def __init__(self) -> None:
        self.vacuum: Vacuum | None = None
        self.transport: str | None = None

    def build(self, vacuum: Vacuum):
        self.vacuum = vacuum
        return self

    def run(self, transport: str, show_banner: bool) -> None:
        self.transport = transport


@pytest.fixture
def started(monkeypatch):
    s = _Started()
    monkeypatch.setattr(voice, "build_server", s.build)
    monkeypatch.setattr(voice.logging, "basicConfig", lambda **_: None)
    root = voice.logging.getLogger()
    monkeypatch.setattr(root, "filters", list(root.filters))
    return s


def test_main_simulate_starts_a_pretend_vacuum_over_stdio(started):
    from dreame_mcp.simulator import SimulatedVacuum

    voice.main(["--simulate", "--ask-before-start"])
    assert started.transport == "stdio"
    assert started.vacuum is not None
    assert isinstance(started.vacuum.link.client, SimulatedVacuum)
    assert started.vacuum.simulated and started.vacuum.ask_first


def test_main_reads_the_login_from_the_secrets_folder(started, monkeypatch):
    asked = []
    monkeypatch.setattr(voice, "client_from_env", lambda folder: asked.append(folder) or None)
    voice.main(["--secrets-dir", "/nowhere"])
    assert asked == ["/nowhere"]
    assert started.vacuum is not None
    assert not started.vacuum.simulated and not started.vacuum.ask_first
