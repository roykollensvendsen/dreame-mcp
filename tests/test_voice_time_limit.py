"""An answer within the voice's time (the plugin standard, rule 3).

The Claude app gives up on a tool call after about 15 seconds, and then the
voice has nothing to say. Every tool works to one deadline, CALL_BUDGET from
when it is called: logging in, each cloud call and the pause before reading the
state back all stop at it, and the tool then says what it knows. So the budget
has to fit inside the app's limit, and every tool has to keep to it, even when
the cloud never answers at all.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from fastmcp import Client

from dreame_mcp.client import DreameStatus
from dreame_mcp.voice import CALL_BUDGET, SETTLE, Link, Vacuum, build_server

#: How long the Claude app waits for a tool, in seconds (seen 2026-11).
APP_LIMIT = 15.0
#: Room for what is not counted: the bridge, the network, the MCP round trip.
MARGIN = 1.0

ARGS = {"vacuum_clean_rooms": {"rooms": ["Kitchen"]}, "vacuum_confirm": {"token": "abc123"}}


class Silent:
    """A cloud that never answers: every call waits forever."""

    def __init__(self, *, logged_in: bool) -> None:
        self._logged_in = logged_in

    @property
    def connected(self) -> bool:
        return self._logged_in

    @property
    def cloud_error(self) -> str | None:
        return None

    async def connect(self) -> bool:
        if self._logged_in:
            return True
        await asyncio.Event().wait()
        return False

    async def get_status(self) -> DreameStatus:
        await asyncio.Event().wait()
        raise AssertionError

    async def control(self, cmd: str) -> dict:
        await asyncio.Event().wait()
        return {}

    async def get_map(self) -> dict:
        await asyncio.Event().wait()
        return {}

    async def clean_rooms(self, room_ids: list[int]) -> dict:
        await asyncio.Event().wait()
        return {}

    async def get_full_status(self) -> dict:
        await asyncio.Event().wait()
        return {}

    def disconnect(self) -> None:
        pass


def test_the_budget_fits_in_the_apps_time():
    assert SETTLE < CALL_BUDGET
    assert CALL_BUDGET + MARGIN <= APP_LIMIT


@pytest.mark.parametrize("logged_in", [False, True], ids=["login never answers", "cloud never answers"])
async def test_every_tool_answers_within_the_voices_time(logged_in):
    budget = 0.3
    vacuum = Vacuum(Link(Silent(logged_in=logged_in)), ask_first=True, budget=budget)
    async with Client(build_server(vacuum)) as c:
        for tool in await c.list_tools():
            began = time.monotonic()
            result = await c.call_tool(tool.name, ARGS.get(tool.name, {}), raise_on_error=False)
            took = time.monotonic() - began
            assert took < budget + 0.5, f"{tool.name} took {took:.2f} s"
            answer = result.structured_content or {}
            assert answer.get("say"), f"{tool.name} said nothing"
