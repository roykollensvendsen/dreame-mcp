"""A first line seen whole (the plugin standard, rule 2).

The Claude app lists a tool it has not loaded yet as one line: the connector's
label, then the description, cut at about 80 characters. Some 26 of them are
the description's. So every tool opens with a whole, short sentence on a line
of its own that says what sets it apart, no two tools open with the same word,
and the details follow on the next lines.

``VISIBLE`` is what the voice saw on 2026-11-10. The app may change it, so an
opening is kept short rather than written to fill it.
"""

from __future__ import annotations

import re

from fastmcp import Client

from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Vacuum, build_server

#: Characters of a description the voice saw before loading a tool, 2026-11-10.
VISIBLE = 26


async def _tools() -> list:
    """Every tool in both modes; vacuum_start's text differs with --ask-first."""
    tools = []
    for ask_first in (False, True):
        vacuum = Vacuum(Link(SimulatedVacuum()), ask_first=ask_first, simulated=True, settle=0)
        async with Client(build_server(vacuum)) as c:
            tools += await c.list_tools()
    return tools


def _opening(tool) -> str:
    return (tool.description or "").split("\n", 1)[0]


async def test_every_tool_opens_with_a_whole_sentence_the_voice_sees_in_full():
    for tool in await _tools():
        opening = _opening(tool)
        assert len(opening) <= VISIBLE, f"{tool.name}: {opening!r} is {len(opening)} characters"
        assert re.fullmatch(r"[A-Z].*[.?]", opening), f"{tool.name}: {opening!r} is not a whole sentence"
        assert not re.search(r"[.?;:] ", opening), f"{tool.name}: {opening!r} runs on past one sentence"


async def test_no_two_tools_open_with_the_same_word():
    for ask_first in (False, True):
        vacuum = Vacuum(Link(SimulatedVacuum()), ask_first=ask_first, simulated=True, settle=0)
        async with Client(build_server(vacuum)) as c:
            seen: dict[str, str] = {}
            for tool in await c.list_tools():
                word = _opening(tool).split()[0].strip(",.").casefold()
                assert seen.setdefault(word, tool.name) == tool.name, f"{tool.name} and {seen[word]}"


async def test_the_details_come_on_the_lines_after_the_opening():
    for tool in await _tools():
        rest = (tool.description or "").split("\n", 1)[1:]
        assert rest and rest[0].strip(), f"{tool.name} has no details after its opening"
