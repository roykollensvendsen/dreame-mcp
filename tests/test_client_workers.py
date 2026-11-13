"""A slow map must not use up the client's workers, and maps asked for together share one fetch.

The real limit: the client runs every cloud call on a pool of 4 worker threads. A map
fetch can take up to MAP_FETCH_TIMEOUT (60 s) and holds the map lock all that time,
while the web app asks for the map every 15 s (Map.tsx) and the voice asks for it on
"where is it". A worker waiting for the lock is a worker gone, so a status read or a
command queued behind them waits until it runs out of time.

Offline: the map fetch is a fake that blocks until the test lets it go.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from dreame_mcp.client import DreameHomeClient, DreameStatus


class SlowMap:
    """A map fetch that blocks until released, and counts how often it ran."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.fetches = 0

    def __call__(self) -> dict:
        self.fetches += 1
        self.started.set()
        self.release.wait(timeout=10)
        return {"success": True, "source": "live", "map_data": {"room_list": []}}


@pytest.fixture
def slow():
    c = DreameHomeClient()
    c._protocol = object()
    fetch = SlowMap()
    c._fetch_live_map = fetch  # type: ignore[method-assign]
    c._get_status_sync = lambda: DreameStatus(raw={"2.1": 13})  # type: ignore[method-assign]
    c._control_sync = lambda cmd: {"success": True}  # type: ignore[method-assign]
    yield c, fetch
    fetch.release.set()


async def test_a_slow_map_leaves_the_workers_for_status_and_commands(slow):
    c, fetch = slow
    maps = [asyncio.ensure_future(c.get_map()) for _ in range(5)]  # the web page and the voice
    await asyncio.get_running_loop().run_in_executor(None, fetch.started.wait, 5)
    status = await asyncio.wait_for(c.get_status(), timeout=2)
    assert status.raw == {"2.1": 13}
    assert (await asyncio.wait_for(c.control("stop"), timeout=2))["success"]
    fetch.release.set()
    await asyncio.gather(*maps)


async def test_maps_asked_for_together_share_one_fetch(slow):
    c, fetch = slow
    maps = [asyncio.ensure_future(c.get_map()) for _ in range(3)]
    await asyncio.get_running_loop().run_in_executor(None, fetch.started.wait, 5)
    fetch.release.set()
    answers = await asyncio.wait_for(asyncio.gather(*maps), timeout=5)
    assert all(a["success"] for a in answers)
    assert fetch.fetches == 1


async def test_a_map_after_the_last_one_finished_fetches_again(slow):
    c, fetch = slow
    fetch.release.set()
    await c.get_map()
    await c.get_map()
    assert fetch.fetches == 2
