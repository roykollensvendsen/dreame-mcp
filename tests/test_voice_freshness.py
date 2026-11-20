"""A reading that may be behind is said so, with its age, and a fresh one never is.

The cloud gives no time per reading, so a reading's age is how long ago it was read.
A reading older than a minute is said with that age. A reading the brand knows can lag
(on the Dreame cloud, the water tank and mop while docked, notes n1/n4) is said as last
reported. Each such reading is said once per answer; an untrusted one is never given an
age, because it is not said at all.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any

import pytest

from vacuum_mcp import freshness
from vacuum_mcp.backends import dreame
from vacuum_mcp.readings import FIELDS, Reading, Readings
from vacuum_mcp.voice import BACKEND, Link, Vacuum

NOW = 1_000_000.0


def _reading(value: Any = "fitted", *, age: float = 0, source: str = "fresh", trusted: bool = True) -> Reading:
    return Reading(value, trusted, NOW - age, source)  # type: ignore[arg-type]


# -- the words -------------------------------------------------------------------


def test_a_reading_older_than_a_minute_is_said_with_its_age():
    assert freshness.when(_reading(age=90), NOW) == "as of 1 minute ago"
    assert freshness.when(_reading(age=200), NOW) == "as of 3 minutes ago"
    assert freshness.when(_reading(age=60), NOW) is None  # a minute is still fresh


def test_under_a_minute_the_age_is_said_in_seconds():
    assert freshness.age_words(45) == "45 seconds"
    assert freshness.age_words(1) == "1 second"
    assert freshness.age_words(3600) == "60 minutes"


def test_a_cached_reading_is_said_as_last_reported():
    assert freshness.when(_reading(source="cache"), NOW) == "when last reported"
    # Old and cached: the age is the better thing to say.
    assert freshness.when(_reading(age=200, source="cache"), NOW) == "as of 3 minutes ago"


def test_a_fresh_reading_is_never_said_as_behind():
    assert freshness.when(_reading(), NOW) is None
    assert freshness.when(_reading(source="push"), NOW) is None
    assert freshness.note([("the water tank showed fitted", _reading())], NOW) == ""


def test_an_untrusted_or_empty_reading_gets_no_age():
    assert freshness.when(_reading(source="cache", trusted=False), NOW) is None
    assert freshness.when(_reading(None, source="cache"), NOW) is None


def test_the_note_says_each_reading_once_and_the_cloud_may_be_behind():
    cached = _reading(source="cache")
    note = freshness.note(
        [
            ("the water tank showed fitted", cached),
            ("the water tank showed fitted", cached),
            ("the mop pad showed attached", cached),
            ("the battery showed 84 percent", _reading(84, age=200)),
            ("the water flow showed medium", _reading("medium")),
        ],
        NOW,
    )
    assert note == (
        "The water tank showed fitted and the mop pad showed attached when last reported; "
        "the battery showed 84 percent as of 3 minutes ago; the cloud may be behind."
    )
    assert note.count("the water tank") + note.count("The water tank") == 1


# -- the backend says which readings lag ----------------------------------------------

#: Docked and charging (2.1 = 6, 3.2 = 1), the tank fitted (4.6 = 1), battery 84.
DOCKED = {"2.1": 6, "3.2": 1, "3.1": 84, "4.6": 1}
#: Cleaning, off the dock (2.1 = 1, 3.2 = 2).
OFF_DOCK = {"2.1": 1, "3.2": 2, "3.1": 70, "4.6": 1}


def test_the_tank_and_mop_on_the_dock_come_from_the_cache():
    docked = dreame.read(DOCKED, "dreame.vacuum.r2363a", NOW, "fresh")
    assert docked.water_tank.source == "cache"
    assert docked.mop_pad.source == "cache"
    assert docked.battery.source == "fresh"
    assert docked.state.source == "fresh"
    away = dreame.read(OFF_DOCK, "dreame.vacuum.r2363a", NOW, "fresh")
    assert {getattr(away, f).source for f in FIELDS} == {"fresh"}


# -- the voice says it -------------------------------------------------------------


class Client:
    """A logged-in client whose cloud answers with the given properties."""

    model: str | None = "dreame.vacuum.r2363a"

    def __init__(self, raw: dict[str, Any]):
        self.raw = raw
        self.connected = True
        self.cloud_error = None

    async def connect(self) -> bool:
        return True

    async def get_status(self) -> Any:
        from dreame_mcp.client import DreameStatus

        return DreameStatus(raw=dict(self.raw))

    async def get_full_status(self) -> dict:
        return {"success": True, "raw": dict(self.raw), "model": self.model}

    async def get_map(self) -> dict:
        return {"success": False, "error": "no map here"}

    def disconnect(self) -> None:
        pass


def _vacuum(raw: dict[str, Any]) -> Vacuum:
    return Vacuum(Link(Client(raw)), settle=0)


BEHIND = "the cloud may be behind"


async def test_a_docked_full_status_says_the_tank_was_last_reported():
    say = (await _vacuum(DOCKED).full_status())["say"]
    assert "The water tank showed fitted when last reported; the cloud may be behind." in say
    assert say.count(BEHIND) == 1


async def test_an_off_dock_full_status_does_not_say_the_cloud_may_be_behind():
    say = (await _vacuum(OFF_DOCK).full_status())["say"]
    assert BEHIND not in say


async def test_a_status_with_fresh_readings_does_not_say_the_cloud_may_be_behind():
    say = (await _vacuum(DOCKED).status())["say"]
    assert BEHIND not in say


@pytest.fixture
def three_minutes_old(monkeypatch):
    """Every reading as if it was read three minutes ago."""
    read = BACKEND.read

    def old(raw, model, as_of, source) -> Readings:
        got = read(raw, model, as_of, source)
        return Readings(**{f: replace(getattr(got, f), as_of=time.time() - 180) for f in FIELDS})

    monkeypatch.setattr(BACKEND, "read", old)


async def test_an_old_status_is_said_with_its_age(three_minutes_old):
    say = (await _vacuum(OFF_DOCK).status())["say"]
    assert say.endswith(
        "It showed cleaning and the battery showed 70 percent as of 3 minutes ago; the cloud may be behind."
    )
    assert say.count(BEHIND) == 1


async def test_an_old_full_status_says_each_reading_once(three_minutes_old):
    say = (await _vacuum(DOCKED).full_status())["say"]
    assert say.count(BEHIND) == 1
    assert say.count("the water tank showed fitted") + say.count("The water tank showed fitted") == 1
    assert "as of 3 minutes ago" in say
