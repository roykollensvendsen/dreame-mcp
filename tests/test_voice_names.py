"""Names the voice can tell apart (the plugin standard, rule 1).

The voice picks a tool by its name and the first line of its description, often
from something heard in a car. Names that sound alike, such as vacuum_start and
vacuum_stop, are the ones it can mix up. Each such pair is listed here, with
what keeps the two apart, so a new look-alike cannot slip in unnoticed.
"""

from __future__ import annotations

import difflib
import itertools

from fastmcp import Client

from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Vacuum, build_server

#: Every pair of tools whose names sound alike, and what tells them apart.
LOOK_ALIKE: dict[frozenset[str], str] = {
    frozenset({"vacuum_start", "vacuum_stop"}): (
        "the riskiest pair: 'Full clean of the home.' against 'Stop where it is.'"
    ),
    frozenset({"vacuum_start", "vacuum_status"}): (
        "'Full clean of the home.' against 'State and battery now.'; status only reads"
    ),
    frozenset({"vacuum_status", "vacuum_stop"}): (
        "'State and battery now.' against 'Stop where it is.'; status only reads"
    ),
    frozenset({"vacuum_rooms", "vacuum_clean_rooms"}): (
        "'List the map's rooms.' against 'Clean only named rooms.'; the clean needs room names"
    ),
}


def _word(name: str) -> str:
    return name.removeprefix("vacuum_")


def sound_alike(a: str, b: str) -> bool:
    """Names start with the same sound, or are mostly the same letters."""
    a, b = _word(a), _word(b)
    return a[:2] == b[:2] or difflib.SequenceMatcher(None, a, b).ratio() >= 0.6


async def _tools() -> dict[str, str]:
    """Every tool the server can offer, with its first line, in both modes."""
    tools: dict[str, str] = {}
    for ask_first in (False, True):
        vacuum = Vacuum(Link(SimulatedVacuum()), ask_first=ask_first, simulated=True, settle=0)
        async with Client(build_server(vacuum)) as c:
            for t in await c.list_tools():
                tools[t.name] = (t.description or "").splitlines()[0]
    return tools


def test_the_check_finds_start_and_stop():
    assert sound_alike("vacuum_start", "vacuum_stop")
    assert not sound_alike("vacuum_find", "vacuum_where")


async def test_names_that_sound_alike_are_listed():
    tools = await _tools()
    found = {frozenset(p) for p in itertools.combinations(sorted(tools), 2) if sound_alike(*p)}
    assert found == set(LOOK_ALIKE), (
        f"unlisted: {sorted(map(sorted, found - set(LOOK_ALIKE)))}, "
        f"listed but gone: {sorted(map(sorted, set(LOOK_ALIKE) - found))}"
    )


async def test_names_that_sound_alike_open_with_different_words():
    tools = await _tools()
    for pair in LOOK_ALIKE:
        a, b = sorted(pair)
        first_a, first_b = (tools[n].casefold().split()[0].strip(",.") for n in (a, b))
        assert first_a != first_b, (a, tools[a], b, tools[b])
