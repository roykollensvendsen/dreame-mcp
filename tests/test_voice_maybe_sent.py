"""A command whose answer never came back: the voice says it may have reached the vacuum.

The real client hands a command to a worker thread and gives up waiting after a while,
but the thread carries on and the request can still reach the robot (client.py,
EXECUTOR_TIMEOUT). A lost answer also comes back as "No response". In both cases
"The vacuum wasn't told anything" would be untrue.

Offline: a fake client whose robot counts what reaches it, never the real robot or cloud.
"""

from __future__ import annotations

import asyncio
import time

from dreame_mcp.client import DreameStatus
from dreame_mcp.voice import Link, Vacuum

NOT_SENT = "wasn't told anything"


class LostAnswer:
    """Logged in, reads the state at once; a command reaches the robot, then its answer is
    lost: it hangs (`hang`) or comes back as the real client's "No response"."""

    connected = True
    cloud_error = None

    def __init__(self, *, hang: bool, state: int = 13):
        self.hang = hang
        self.state = state
        self.robot: list[str] = []

    async def connect(self) -> bool:
        return True

    async def get_status(self) -> DreameStatus:
        charging = 3 if self.state == 13 else 2  # fully charged on the dock, else off it
        return DreameStatus(raw={"2.1": self.state, "3.1": 90, "3.2": charging})

    async def _lost(self, cmd: str) -> dict:
        self.robot.append(cmd)
        if self.hang:
            await asyncio.Event().wait()
        return {"success": False, "error": f"No response for {cmd}"}

    async def control(self, cmd: str) -> dict:
        return await self._lost(cmd)

    async def clean_rooms(self, room_ids: list[int]) -> dict:
        return await self._lost(f"clean_rooms:{room_ids}")

    async def get_map(self) -> dict:
        return {"success": True, "source": "live", "map_data": {"room_list": [{"id": 2, "name": "Kitchen"}]}}

    def disconnect(self) -> None:
        pass


def _vacuum(client, budget=0.3) -> Vacuum:
    return Vacuum(Link(client, clock=time.monotonic), settle=0, budget=budget, clock=time.monotonic)


async def test_a_command_that_may_have_gone_says_so():
    fake = LostAnswer(hang=True)
    a = await _vacuum(fake).start()
    assert fake.robot == ["start_clean"]
    assert not a["success"]
    assert NOT_SENT not in a["say"]
    assert "may still reach the vacuum" in a["say"]
    assert a["say"].endswith("Ask me for its status before you try again.")


async def test_a_command_without_an_answer_may_have_gone():
    fake = LostAnswer(hang=False, state=1)  # cleaning, so stop applies
    a = await _vacuum(fake).stop()
    assert fake.robot == ["stop"]
    assert NOT_SENT not in a["say"]
    assert "may have reached the vacuum" in a["say"]


async def test_a_room_clean_that_may_have_gone_says_so():
    fake = LostAnswer(hang=True)
    a = await _vacuum(fake).clean_rooms(["Kitchen"])
    assert fake.robot == ["clean_rooms:[2]"]
    assert NOT_SENT not in a["say"]
    assert "may still reach the vacuum" in a["say"]


async def test_a_go_home_without_an_answer_may_have_gone():
    fake = LostAnswer(hang=False, state=1)
    a = await _vacuum(fake).go_home()
    assert fake.robot == ["go_home"]
    assert NOT_SENT not in a["say"]
