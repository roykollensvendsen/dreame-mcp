"""Read-only is marked (the plugin standard, rule 9).

A tool that changes nothing carries readOnlyHint, so a client may call it
without asking; every other tool says it acts. The lists are written out here
rather than read from the server, so marking a command read-only by mistake
fails this test.
"""

from __future__ import annotations

from fastmcp import Client

from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Vacuum, build_server

READ_ONLY = {"vacuum_status", "vacuum_where", "vacuum_rooms"}
ACTS = {
    "vacuum_start",
    "vacuum_confirm",
    "vacuum_pause",
    "vacuum_stop",
    "vacuum_go_home",
    "vacuum_find",
    "vacuum_clean_rooms",
}


async def test_a_tool_that_changes_nothing_is_marked_read_only():
    vacuum = Vacuum(Link(SimulatedVacuum()), ask_first=True, simulated=True, settle=0)
    async with Client(build_server(vacuum)) as c:
        tools = {t.name: t.annotations for t in await c.list_tools()}
    assert set(tools) == READ_ONLY | ACTS
    for name, notes in tools.items():
        assert notes is not None, name
        assert notes.readOnlyHint is (name in READ_ONLY), name
        if name in ACTS:
            assert notes.destructiveHint is False, name
