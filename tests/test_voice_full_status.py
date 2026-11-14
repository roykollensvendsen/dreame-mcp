"""The full status: everything the vacuum's cloud reports, problems first.

The real limit: ``RECORDED`` is what the Dreame cloud answered for Roy's L10s Plus
(dreame.vacuum.r2363a) on 2026-11-14, one read-only get_properties per property, with
the answer code kept and the device id left out. The fakes answer from it; no cloud is
reached. What each number means is in client.py beside ``FULL_STATUS``.
"""

from __future__ import annotations

from typing import Any

import pytest

from dreame_mcp.client import FULL_STATUS, DreameHomeClient
from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Vacuum

#: siid.piid -> (answer code, value), as the cloud answered on 2026-11-14 (the robot on its
#: dock, fully charged, its water tank just refilled and fitted).
RECORDED: dict[str, tuple[int, Any]] = {
    "2.1": (0, 13), "2.2": (0, 0), "3.1": (0, 100), "3.2": (0, 1),
    "4.1": (0, 6), "4.5": (0, 2), "4.6": (0, 1), "4.7": (0, 0), "4.18": (0, "0"),
    "4.25": (0, 0), "4.35": (0, 101), "4.38": (0, 0), "4.41": (0, 0), "4.45": (0, 0),
    "4.52": (0, 0), "4.53": (0, 0), "4.54": (-1, None), "4.56": (0, 0), "4.57": (0, ""),
    "4.60": (0, 0),
    "9.1": (0, 263), "9.2": (0, 87), "10.1": (0, 163), "10.2": (0, 81),
    "11.1": (0, 75), "11.2": (0, 113),
    "15.1": (0, 1), "15.2": (0, 1), "15.3": (0, 1), "15.5": (0, 0),
    "16.1": (0, 0), "16.2": (0, 0), "18.1": (0, 100), "18.2": (0, 80),
    "20.1": (0, 100), "20.2": (0, 18),
    "27.1": (0, 3), "27.2": (0, 0), "27.3": (0, 0), "27.4": (0, 0), "27.5": (0, 0), "27.9": (0, 1),
}  # fmt: skip


def _cloud_answer(props: list[dict]) -> list[dict]:
    """What the cloud sends back for a get_properties: a value only where it has one."""
    out = []
    for p in props:
        code, value = RECORDED.get(f"{p['siid']}.{p['piid']}", (-1, None))
        out.append({**p, "code": code, **({"value": value} if code == 0 else {})})
    return out


# -- the client reads it ----------------------------------------------------------


def _client(monkeypatch, answer=_cloud_answer) -> tuple[DreameHomeClient, list]:
    c = DreameHomeClient(did="42")
    c._protocol = object()
    asked: list = []

    def call(method, verb, props):
        asked.append(props)
        return answer(props)

    monkeypatch.setattr(c, "_safe_call", call)
    return c, asked


def test_the_full_status_reads_every_property_it_names(monkeypatch):
    c, asked = _client(monkeypatch)
    got = c._get_full_status_sync()
    assert got["success"]
    assert [(p["siid"], p["piid"]) for chunk in asked for p in chunk] == list(FULL_STATUS)
    assert all(len(chunk) <= 10 for chunk in asked)  # the most one read asked for on 2026-11-14
    assert got["raw"]["4.6"] == 1 and got["raw"]["16.1"] == 0


def test_a_property_the_cloud_has_no_value_for_is_left_out(monkeypatch):
    c, _ = _client(monkeypatch, lambda props: [{**p, "code": -1} for p in props])
    assert c._get_full_status_sync()["raw"] == {}


def test_no_full_status_when_the_cloud_does_not_answer(monkeypatch):
    c, _ = _client(monkeypatch, lambda props: None)
    assert not c._get_full_status_sync()["success"]


def test_no_full_status_without_a_connection():
    assert not DreameHomeClient()._get_full_status_sync()["success"]


# -- the voice says it ------------------------------------------------------------


class Recorded:
    """A logged-in client whose cloud answers as the robot did on 2026-11-14."""

    def __init__(self, **change: Any):
        self.raw = {k: v for k, (code, v) in RECORDED.items() if code == 0}
        self.raw.update(change)
        self.connected = True
        self.cloud_error = None
        self.map_source = "live"

    async def connect(self) -> bool:
        return True

    async def get_full_status(self) -> dict:
        return {"success": True, "raw": dict(self.raw)}

    async def get_map(self) -> dict:
        self.map_reads = getattr(self, "map_reads", 0) + 1
        return {"success": True, "source": self.map_source, "map_data": {"room_list": ROOMS, "robot_room": ROOMS[1]}}

    def disconnect(self) -> None:
        pass


#: Made up, never a real map: two rooms, the robot in the second.
ROOMS = [{"id": 1, "name": "Room 1"}, {"id": 2, "name": "Room 2"}]
#: A clean in progress, made up: cleaning (2.1 = 1), 25 minutes, 21 square metres, 40 percent done.
CLEANING = {"2.1": 1, "3.2": 2, "4.2": 25, "4.3": 21, "4.63": 40, "4.35": 0, "16.1": 100}


async def _say(**change: Any) -> dict:
    return await Vacuum(Link(Recorded(**change)), settle=0).full_status()


async def test_the_recorded_answer_reads_right():
    answer = await _say()
    assert answer["success"]
    say = answer["say"]
    assert "dust bin is full" in say
    assert "sensors" in say
    assert "water tank fitted" in say
    assert "mop pad not attached" in say
    assert "main brush 87, side brush 81, filter 75, mop pad 100, sensors 0 percent" in say
    assert answer["water_tank"] == "fitted"
    assert answer["wear"]["filter"] == {"percent": 75, "hours": 113}
    assert answer["docked"] is True


async def test_a_full_status_says_problems_first():
    say = (await _say())["say"]
    assert say.startswith("Problems:")
    assert say.index("dust bin is full") < say.index("on the dock")


async def test_without_a_problem_it_says_so():
    say = (await _say(**{"4.35": 0, "16.1": 100}))["say"]
    assert say.startswith("No problems.")


async def test_a_missing_water_tank_is_a_problem():
    answer = await _say(**{"4.6": 0, "4.35": 0, "16.1": 100})
    assert answer["say"].startswith("Problems: the water tank is out.")
    assert answer["water_tank"] == "out"


async def test_a_part_at_ten_percent_or_less_is_due():
    say = (await _say(**{"4.35": 0, "16.1": 100, "9.2": 10}))["say"]
    assert say.startswith("Problems: the main brush is due for a change.")
    say = (await _say(**{"4.35": 0, "16.1": 100, "9.2": 11}))["say"]
    assert say.startswith("No problems.")


async def test_an_error_is_a_problem():
    say = (await _say(**{"2.2": 80, "4.35": 0, "16.1": 100}))["say"]
    assert say.startswith("Problems: it is stuck.")
    say = (await _say(**{"2.2": 333, "4.35": 0, "16.1": 100}))["say"]
    assert "error code 333" in say


async def test_a_full_status_says_what_it_cannot_report():
    answer = await _say()
    assert answer["say"].endswith(
        "It can't report the water level or how full the bin is, and it doesn't read the base's water tanks."
    )
    assert set(answer["not_reported"]) == {"water level", "bin fill", "base water tanks"}


async def test_a_reading_the_cloud_left_out_is_not_guessed():
    vacuum = Recorded()
    del vacuum.raw["4.6"], vacuum.raw["9.2"]
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert answer["water_tank"] is None
    assert "water tank" not in answer["say"].split("It can't")[0]
    assert "main_brush" not in answer["wear"]


@pytest.mark.parametrize("offline", [False, True])
async def test_the_pretend_vacuum_has_a_full_status(offline):
    answer = await Vacuum(Link(SimulatedVacuum(offline=offline)), simulated=True, settle=0).full_status()
    assert answer["success"] is not offline
    assert answer["say"].startswith("Simulated: ")


# -- during a clean -----------------------------------------------------------------


async def test_a_clean_in_progress_says_how_far_it_has_got():
    answer = await _say(**CLEANING)
    say = answer["say"]
    assert say.startswith(
        "No problems. It is cleaning, battery 100 percent; 40 percent done, 21 square metres in 25 minutes; now in Room 2;"
    )
    assert answer["progress"] == {"percent": 40, "square_metres": 21, "minutes": 25}
    assert answer["room"] == "Room 2"


async def test_a_clean_in_progress_says_what_it_cannot_report():
    answer = await _say(**CLEANING)
    assert answer["say"].endswith(
        "It can't report which rooms are done or how long is left, the water level or how full the bin is,"
        " and it doesn't read the base's water tanks."
    )
    assert {"rooms done", "time left"} <= set(answer["not_reported"])


async def test_a_full_status_names_the_room_only_from_the_live_map():
    vacuum = Recorded(**CLEANING)
    vacuum.map_source = "saved"
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert "Room 2" not in answer["say"]
    assert answer["room"] is None


async def test_on_the_dock_there_is_no_progress_and_no_map_read():
    vacuum = Recorded()
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert "percent done" not in answer["say"]
    assert answer["progress"] is None
    assert getattr(vacuum, "map_reads", 0) == 0


async def test_a_map_that_cannot_be_had_still_gives_the_progress():
    vacuum = Recorded(**CLEANING)

    async def no_map() -> dict:
        return {"success": False, "error": "no map"}

    vacuum.get_map = no_map  # type: ignore[method-assign]
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert answer["success"]
    assert "40 percent done, 21 square metres in 25 minutes;" in answer["say"]
    assert "now in" not in answer["say"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"27.3": 1}, "the dust bag in the base is not fitted"),
        ({"27.3": 2}, "the dust bag in the base is to be checked"),
        ({"15.5": 2}, "the base didn't empty the bin"),
        ({"4.41": 5}, "the water tank is low"),
    ],
)
async def test_the_base_and_the_water_say_their_problems(change, problem):
    say = (await _say(**{"4.35": 0, "16.1": 100, **change}))["say"]
    assert say.startswith(f"Problems: {problem}.")


async def test_a_full_status_the_cloud_does_not_give_says_what_to_do():
    vacuum = Recorded()

    async def nothing() -> dict:
        return {"success": False, "error": "No response from device"}

    vacuum.get_full_status = nothing  # type: ignore[method-assign]
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert answer == {"success": False, "say": "I can't reach the Dreame cloud right now. Try again in a minute."}


async def test_the_pretend_vacuum_reports_how_far_its_clean_has_got():
    now = [0.0]
    pretend = SimulatedVacuum(clock=lambda: now[0])
    vacuum = Vacuum(Link(pretend), simulated=True, settle=0)
    await vacuum.start()
    now[0] += 600
    answer = await vacuum.full_status()
    assert answer["progress"] == {"percent": 20, "square_metres": 10, "minutes": 10}


async def test_the_pretend_vacuum_gives_no_full_status_before_it_is_logged_in():
    assert (await SimulatedVacuum().get_full_status())["success"] is False


async def test_what_it_cannot_report_holds_for_any_model():
    # A fact about one model would be said aloud on every other one: the words name none.
    for cleaning in ({}, CLEANING):
        say = (await _say(**cleaning))["say"]
        tail = say[say.index("It can't") :]
        assert not any(model in tail for model in ("L10", "Plus", "Ultra", "r2363a"))


async def test_a_missing_hours_reading_is_left_out_not_zero():
    vacuum = Recorded()
    del vacuum.raw["11.2"]
    answer = await Vacuum(Link(vacuum), settle=0).full_status()
    assert answer["wear"]["filter"] == {"percent": 75}
