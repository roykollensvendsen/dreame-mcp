"""A login session that runs out: the voice logs in again instead of failing until a restart.

The Dreame cloud's session ends after a while. The client library renews it only in
the last ten minutes before it ends, and only when a request comes in then
(protocol.py, _refresh_expired_key), so a quiet night can let it lapse. After that,
every request fails while `connected` may still say yes; a "session invalid" answer
makes it say no (protocol.py, request on a 401).

Offline: a fake whose session ends on cue, never the real robot or cloud.
"""

from __future__ import annotations

from dreame_mcp.client import DreameStatus
from dreame_mcp.voice import Link, Vacuum


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class ExpiringSession:
    """Each login gives a session that ends `lifetime` seconds later. After that every
    request fails as the real client's do: no status, no answer to a command, no map.
    `connected` stays yes unless `says_invalid`, as after a 401."""

    def __init__(self, clock: Clock, *, lifetime: float = 3600, says_invalid: bool = False):
        self.clock = clock
        self.lifetime = lifetime
        self.says_invalid = says_invalid
        self.logins = 0
        self.ends: float | None = None
        self.robot: list[str] = []
        self.cloud_error = None

    @property
    def live(self) -> bool:
        return self.ends is not None and self.clock() < self.ends

    @property
    def connected(self) -> bool:
        return self.live if self.says_invalid else self.ends is not None

    async def connect(self) -> bool:
        self.logins += 1
        self.ends = self.clock() + self.lifetime
        return True

    async def get_status(self) -> DreameStatus:
        if not self.live:
            return DreameStatus(error="No response")
        return DreameStatus(raw={"2.1": 1, "3.1": 80, "3.2": 2})  # cleaning, off the dock

    async def control(self, cmd: str) -> dict:
        if not self.live:
            return {"success": False, "error": f"No response for {cmd}"}
        self.robot.append(cmd)
        return {"success": True}

    async def clean_rooms(self, room_ids: list[int]) -> dict:
        return await self.control(f"clean_rooms:{room_ids}")

    async def get_map(self) -> dict:
        if not self.live:
            return {"success": False, "error": "no map"}
        return {"success": True, "source": "live", "map_data": {"room_list": [{"id": 2, "name": "Kitchen"}]}}

    def disconnect(self) -> None:
        self.ends = None


def _vacuum(fake: ExpiringSession, clock: Clock) -> Vacuum:
    return Vacuum(Link(fake, clock=clock), settle=0, clock=clock)


async def _lapse(v: Vacuum, fake: ExpiringSession, clock: Clock) -> None:
    assert (await v.status())["success"]  # logged in once
    clock.now += fake.lifetime + 1  # a quiet night: the session runs out


async def test_a_failed_command_logs_in_again_on_the_next_call():
    clock = Clock()
    fake = ExpiringSession(clock)
    v = _vacuum(fake, clock)
    await _lapse(v, fake, clock)
    assert not (await v.find())["success"]  # the first call after it ran out fails
    a = await v.find()
    assert a["success"], a["say"]
    assert fake.logins == 2
    assert fake.robot == ["find_robot"]


class MapLapses(ExpiringSession):
    """The status still answers after the session ran out; only the map fails."""

    async def get_status(self) -> DreameStatus:
        return DreameStatus(raw={"2.1": 1, "3.1": 80, "3.2": 2})


async def test_a_failed_map_logs_in_again_on_the_next_call():
    clock = Clock()
    fake = MapLapses(clock)
    v = _vacuum(fake, clock)
    await _lapse(v, fake, clock)
    assert not (await v.rooms())["success"]
    a = await v.rooms()
    assert a["success"], a["say"]
    assert fake.logins == 2


async def test_a_session_the_cloud_called_invalid_logs_in_again_at_once():
    clock = Clock()
    fake = ExpiringSession(clock, says_invalid=True)
    v = _vacuum(fake, clock)
    await _lapse(v, fake, clock)
    a = await v.status()
    assert a["success"], a["say"]
    assert fake.logins == 2


async def test_a_lapsed_session_logs_in_again_at_most_once_a_minute():
    clock = Clock()
    fake = ExpiringSession(clock, lifetime=0.5)  # every new session runs out at once
    v = _vacuum(fake, clock)
    await v.status()
    clock.now += 1
    for _ in range(5):
        await v.find()
        clock.now += 1
    assert fake.logins <= 2
