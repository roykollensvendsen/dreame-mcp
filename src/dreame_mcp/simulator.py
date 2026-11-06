"""A pretend Dreame vacuum, for trying the voice server and testing it without a robot or a login.

It answers like DreameHomeClient: ``connect``, ``get_status`` and ``control``
with the same command names and the same raw property keys ("2.1" state,
"3.1" battery, "3.2" charging status, "2.2" error). It moves through the
states on a clock:

- on the dock it charges, 1 percent every 6 s, and is fully charged at 100;
- cleaning uses 1 percent every 12 s, and below 15 percent it heads home;
- heading home takes 20 s, then it charges;
- pause holds it where it is, stop leaves it idle off the dock.

``offline=True`` makes every login fail, for trying the "can't reach the
cloud" answer.

Its map has three rooms. The dock is in the living room; while cleaning it
moves from room to room every 30 s, and heading home it is in the hallway.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from .client import DreameStatus

SWEEPING, IDLE, PAUSED, RETURNING, CHARGING_STATE, CHARGED_STATE = 1, 2, 3, 5, 6, 13
CHARGING, NOT_CHARGING, CHARGED = 1, 2, 3
CHARGE_EVERY = 6.0
DRAIN_EVERY = 12.0
TRIP_HOME = 20.0
LOW_BATTERY = 15
ROOMS = [{"id": 1, "name": "Living room"}, {"id": 2, "name": "Kitchen"}, {"id": 3, "name": "Hallway"}]
DOCK_ROOM, HALL = ROOMS[0], ROOMS[2]
ROOM_EVERY = 30.0


class SimulatedVacuum:
    def __init__(
        self,
        *,
        battery: float = 100.0,
        docked: bool = True,
        offline: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._clock = clock
        self._battery = battery
        self._state = CHARGED_STATE if docked else IDLE
        self._since = clock()
        self._last = clock()
        self._offline = offline
        self._connected = False
        self.sent: list[str] = []

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def cloud_error(self) -> str | None:
        return "simulated: cloud unreachable" if self._offline else None

    async def connect(self) -> bool:
        self._connected = not self._offline
        return self._connected

    def disconnect(self) -> None:
        self._connected = False

    def _advance(self) -> None:
        now = self._clock()
        dt, self._last = now - self._last, now
        if self._state in (CHARGING_STATE, CHARGED_STATE):
            self._battery = min(100.0, self._battery + dt / CHARGE_EVERY)
            self._state = CHARGED_STATE if self._battery >= 100 else CHARGING_STATE
        elif self._state == SWEEPING:
            self._battery = max(0.0, self._battery - dt / DRAIN_EVERY)
            if self._battery < LOW_BATTERY:
                self._go(RETURNING)
        elif self._state == RETURNING and now - self._since >= TRIP_HOME:
            self._go(CHARGING_STATE)

    def _go(self, state: int) -> None:
        self._state = state
        self._since = self._clock()

    async def get_status(self) -> DreameStatus:
        if not self._connected:
            return DreameStatus(error="Not connected")
        self._advance()
        charging = {CHARGING_STATE: CHARGING, CHARGED_STATE: CHARGED}.get(self._state, NOT_CHARGING)
        battery = int(self._battery)
        raw = {"2.1": self._state, "2.2": 0, "3.1": battery, "3.2": charging}
        return DreameStatus(
            state=str(self._state),
            battery=battery,
            is_charging=charging == CHARGING,
            is_cleaning=self._state == SWEEPING,
            raw=raw,
        )

    async def control(self, cmd: str) -> dict:
        if not self._connected:
            return {"success": False, "error": "Not connected"}
        self._advance()
        moves = {
            "start_clean": SWEEPING,
            "pause": PAUSED,
            "stop": IDLE,
            "go_home": RETURNING,
        }
        if cmd in moves:
            self._go(moves[cmd])
        elif cmd != "find_robot":
            return {"success": False, "error": f"Unknown command: {cmd}"}
        self.sent.append(cmd)
        return {"success": True, "message": f"Sent {cmd}"}

    async def get_map(self) -> dict:
        if not self._connected:
            return {"success": False, "error": "Not connected"}
        self._advance()
        docked = self._state in (CHARGING_STATE, CHARGED_STATE)
        if docked:
            room = DOCK_ROOM
        elif self._state == RETURNING:
            room = HALL
        else:
            room = ROOMS[int((self._clock() - self._since) // ROOM_EVERY) % len(ROOMS)]
        return {
            "success": True,
            "source": "live",
            "object_name": "simulated",
            "map_data": {"rooms": len(ROOMS), "room_list": ROOMS, "robot_room": room, "docked": docked},
        }
