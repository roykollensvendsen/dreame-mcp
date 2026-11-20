"""A command the vacuum's model can't do is refused in words, and nothing is sent.

Every tool is always offered, so the tool list and the owner's approval never change with
the robot. The model's capabilities decide whether a tool acts or says it can't.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from dreame_mcp.simulator import SimulatedVacuum
from vacuum_mcp.backends import dreame
from vacuum_mcp.capabilities import NOTHING, Capabilities
from vacuum_mcp.voice import BACKEND, Link, Vacuum, build_server

#: Each tool that acts, its arguments, and the command it needs.
ACTING = [
    ("vacuum_clean_all", {}, "start"),
    ("vacuum_clean_rooms", {"rooms": ["Kitchen"]}, "start_rooms"),
    ("vacuum_pause", {}, "pause"),
    ("vacuum_stop", {}, "stop"),
    ("vacuum_go_home", {}, "dock"),
    ("vacuum_find", {}, "locate"),
]


def _vacuum(sim: SimulatedVacuum, *, ask_first: bool = False) -> Vacuum:
    return Vacuum(Link(sim), ask_first=ask_first, settle=0)


async def _call(vacuum: Vacuum, tool: str, args: dict) -> dict:
    async with Client(build_server(vacuum)) as c:
        return (await c.call_tool(tool, args)).structured_content


@pytest.mark.parametrize(("tool", "args", "command"), ACTING)
async def test_a_command_the_model_lacks_sends_nothing(monkeypatch, tool, args, command):
    monkeypatch.setattr(BACKEND, "capabilities", lambda model: NOTHING)
    sim = SimulatedVacuum()
    answer = await _call(_vacuum(sim), tool, args)
    assert answer["success"] is False
    assert answer["say"].startswith("This vacuum can't ")
    assert "wasn't told anything" in answer["say"]
    assert answer["say"].endswith(f"Use {dreame.APP} for that instead.")
    assert sim.sent == []


@pytest.mark.parametrize(("tool", "args", "command"), ACTING)
async def test_only_the_missing_command_is_refused(monkeypatch, tool, args, command):
    others = Capabilities(commands=frozenset({c for _, _, c in ACTING} - {command}), readings=frozenset())
    monkeypatch.setattr(BACKEND, "capabilities", lambda model: others)
    answer = await _call(_vacuum(SimulatedVacuum()), tool, args)
    assert answer["say"].startswith("This vacuum can't ")


async def test_reading_tools_answer_on_a_model_that_can_do_nothing(monkeypatch):
    monkeypatch.setattr(BACKEND, "capabilities", lambda model: NOTHING)
    for tool in ("vacuum_status", "vacuum_where", "vacuum_rooms"):
        assert (await _call(_vacuum(SimulatedVacuum()), tool, {}))["success"] is True, tool


async def test_a_confirm_checks_again_and_sends_nothing_once_the_command_is_gone(monkeypatch):
    sim = SimulatedVacuum()
    vacuum = _vacuum(sim, ask_first=True)
    async with Client(build_server(vacuum)) as c:
        asked = (await c.call_tool("vacuum_clean_all", {})).structured_content
        assert asked["question"] is True
        monkeypatch.setattr(BACKEND, "capabilities", lambda model: NOTHING)
        answer = (await c.call_tool("vacuum_confirm", {"token": asked["token"]})).structured_content
    assert answer["success"] is False
    assert answer["say"].startswith("This vacuum can't ")
    assert sim.sent == []


@pytest.mark.parametrize("model", [None, "dreame.vacuum.never-seen"])
async def test_a_missing_or_unknown_model_keeps_every_command(model):
    assert dreame.capabilities(model).commands == {c for _, _, c in ACTING}
    sim = SimulatedVacuum()
    sim.model = model  # what the login found
    answer = await _call(_vacuum(sim), "vacuum_find", {})
    assert answer["success"] is True
    assert sim.sent == ["find_robot"]


async def test_the_simulator_and_the_cloud_client_say_their_model():
    from dreame_mcp.client import DreameHomeClient
    from dreame_mcp.simulator import MODEL

    assert SimulatedVacuum().model == MODEL
    assert DreameHomeClient.model.fget is not None  # a property, read after login
