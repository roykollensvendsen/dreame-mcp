"""A voice-sized MCP server for a Dreame robot vacuum: eight small tools with spoken answers.

The main server (``server.py``) is a full workbench: one portmanteau tool, a
map, a web app, sampling. A voice assistant needs much less and needs it
short: is the vacuum busy, how full is its battery, start, pause, stop, go
home, beep, where is it, which rooms. This module serves exactly that over stdio, for voice bridges and
other clients that read a one-line answer aloud.

Every tool answers ``{"success", "say", "state", "battery"}``. ``say`` is one
or two sentences to read aloud as they are. When the cloud cannot be reached,
``success`` is false and ``say`` says so, and that nothing was sent.

Run it:

    dreame-voice-mcp --secrets-dir ~/.config/dreame   # login read from files
    dreame-voice-mcp                                  # login from env / .env
    dreame-voice-mcp --simulate                       # a pretend vacuum

``--ask-before-start`` makes ``vacuum_start`` ask first and adds
``vacuum_confirm``, which starts the clean only with the token from that
question.

It logs in to the cloud in the background after starting, so the tool list
comes back at once. Nothing but MCP is written to stdout; logs go to stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from fastmcp import FastMCP

from .client import DreameStatus, client_from_env

logger = logging.getLogger("dreame-mcp.voice")

T = TypeVar("T")

# Answer within about 15 s, so a voice client's own limit never cuts a call off.
CALL_BUDGET = 14.0
# After a failed login, wait this long before trying again: repeated logins
# make the Xiaomi cloud ask for a captcha.
RETRY_AFTER = 60.0
# After a command, wait this long before reading the state back.
SETTLE = 2.0
# A start question stays open this long.
QUESTION_TTL = 120.0

# State codes from the reference project's DreameVacuumState, with a name for
# the answer's 'state' field and words for 'say'.
STATES: dict[int, tuple[str, str]] = {
    1: ("cleaning", "cleaning"),
    2: ("idle", "idle"),
    3: ("paused", "paused"),
    4: ("error", "stopped with a problem"),
    5: ("returning", "on its way back to the dock"),
    6: ("charging", "charging on the dock"),
    7: ("mopping", "mopping"),
    8: ("drying", "drying its mop"),
    9: ("washing", "washing its mop"),
    10: ("returning", "going back to wash its mop"),
    11: ("mapping", "mapping the home"),
    12: ("cleaning", "cleaning"),
    13: ("docked", "on the dock, fully charged"),
    14: ("updating", "updating its software"),
    15: ("cleaning", "cleaning"),
    21: ("paused", "paused while washing its mop"),
    22: ("emptying", "emptying its dust bin"),
    24: ("charging", "charging on the dock"),
    25: ("cleaning", "cleaning a second time"),
    27: ("cleaning", "spot cleaning"),
    28: ("returning", "going back to empty its bin"),
    29: ("idle", "waiting for a task"),
    34: ("emptying", "emptying its dust bin"),
    37: ("cleaning", "heading off for an extra clean"),
    38: ("cleaning", "doing an extra clean"),
    101: ("cleaning", "doing its first deep clean"),
    102: ("paused", "paused in its first deep clean"),
}
# On the floor doing work: a start would be a second start.
CLEANING = frozenset({1, 7, 12, 15, 25, 27, 37, 38, 101})
# Away from the dock and doing something a stop can end.
STOPPABLE = CLEANING | {3, 5, 10, 11, 21, 28, 102}
# On the dock.
DOCKED = frozenset({6, 13, 22, 24, 34})
# Charging status codes (DreameVacuumChargingStatus).
CHARGING, CHARGED = 1, 3

NOTHING_SENT = " The vacuum wasn't told anything."
UNREACHABLE = "I can't reach the Dreame cloud right now."
SIMULATED = "SIMULATED: this is a pretend vacuum, not the real one. Say so when you report on it. "


class VacuumClient(Protocol):
    """What this server needs from a client: DreameHomeClient, or the simulator."""

    @property
    def connected(self) -> bool: ...
    @property
    def cloud_error(self) -> str | None: ...
    async def connect(self) -> bool: ...
    async def get_status(self) -> DreameStatus: ...
    async def control(self, cmd: str) -> dict: ...
    async def get_map(self) -> dict: ...
    def disconnect(self) -> None: ...


class Unavailable(Exception):
    """The vacuum cannot be reached; ``say`` is the spoken reason."""

    def __init__(self, say: str):
        super().__init__(say)
        self.say = say


@dataclass(frozen=True)
class Reading:
    """One status read, reduced to what a spoken answer needs."""

    code: int
    battery: int | None
    charging: int
    error_code: int

    @classmethod
    def of(cls, status: DreameStatus) -> Reading:
        raw = status.raw or {}

        def num(key: str) -> int:
            try:
                return int(raw.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        battery = num("3.1") if "3.1" in raw else None
        return cls(code=num("2.1"), battery=battery, charging=num("3.2"), error_code=num("2.2"))

    @property
    def state(self) -> str:
        if self.code == 2 and self.charging in (CHARGING, CHARGED):
            return "charging" if self.charging == CHARGING else "docked"
        return STATES.get(self.code, (f"state_{self.code}", ""))[0]

    @property
    def docked(self) -> bool:
        return self.code in DOCKED or self.charging in (CHARGING, CHARGED)

    @property
    def cleaning(self) -> bool:
        return self.code in CLEANING

    @property
    def stoppable(self) -> bool:
        return self.code in STOPPABLE

    def words(self) -> str:
        """'charging on the dock, battery 84 percent'."""
        if self.code == 2 and self.charging == CHARGING:
            what = "charging on the dock"
        elif self.code == 2 and self.charging == CHARGED:
            what = "on the dock, fully charged"
        elif self.code in STATES:
            what = STATES[self.code][1]
        else:
            what = f"busy with something I have no name for (state {self.code})"
        if self.code == 4 and self.error_code:
            what += f", error code {self.error_code}; the DreameHome app says what it means"
        if self.battery is not None:
            what += f", battery {self.battery} percent"
        return what


def _login_problem(cloud_error: str | None) -> str:
    """A spoken reason for a failed login, never quoting the account."""
    text = (cloud_error or "").lower()
    if "70016" in text or "credentials" in text:
        return "The Dreame cloud turned down the login. The password on the computer needs checking."
    if "2fa" in text or "verification" in text or "captcha" in text:
        return "The Dreame cloud wants an extra check before it lets me in. Open the DreameHome app."
    return UNREACHABLE


class Link:
    """Keeps the client logged in: in the background at start, again when it has dropped.

    Retries a failed login only after ``retry_after`` seconds, so a string of
    voice requests cannot hammer the cloud into asking for a captcha.
    """

    def __init__(
        self,
        client: VacuumClient | None,
        *,
        retry_after: float = RETRY_AFTER,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.client = client
        self._retry_after = retry_after
        self._clock = clock
        self._task: asyncio.Task[bool] | None = None
        self._last_try: float | None = None

    def start(self) -> None:
        """Begin logging in without waiting for it."""
        if self.client is not None and self._task is None:
            self._begin()

    def _begin(self) -> None:
        assert self.client is not None
        self._last_try = self._clock()
        self._task = asyncio.ensure_future(self._connect(self.client))

    @staticmethod
    async def _connect(client: VacuumClient) -> bool:
        try:
            return await client.connect()
        except Exception:
            logger.exception("connect failed")
            return False

    def mark_stale(self) -> None:
        """A read failed while logged in: log in again on the next call, once the wait is over."""
        if self._task is not None and self._task.done():
            self._task = None

    async def ready(self, deadline: float) -> VacuumClient:
        """The logged-in client, or Unavailable with the spoken reason."""
        client = self.client
        if client is None:
            raise Unavailable("The vacuum isn't set up on the computer yet: its Dreame login is missing.")
        if self._task is None:
            waited = self._clock() - (self._last_try or float("-inf"))
            if waited < self._retry_after and self._last_try is not None:
                raise Unavailable(_login_problem(client.cloud_error))
            self._begin()
        assert self._task is not None
        try:
            ok = await asyncio.wait_for(asyncio.shield(self._task), timeout=max(0.0, deadline - self._clock()))
        except TimeoutError:
            raise Unavailable("I'm still logging in to the Dreame cloud. Ask again in a moment.") from None
        if not ok or not client.connected:
            self._task = None  # a later call may try again, after the wait
            raise Unavailable(_login_problem(client.cloud_error))
        return client

    def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.client is not None:
            try:
                self.client.disconnect()
            except Exception:
                logger.debug("disconnect failed", exc_info=True)


class Vacuum:
    """The actions, each returning a spoken answer."""

    def __init__(
        self,
        link: Link,
        *,
        ask_first: bool = False,
        simulated: bool = False,
        settle: float = SETTLE,
        budget: float = CALL_BUDGET,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.link = link
        self.ask_first = ask_first
        self.simulated = simulated
        self._settle = settle
        self._budget = budget
        self._clock = clock
        self._question: tuple[str, float] | None = None

    # -- answers -------------------------------------------------------

    def _answer(self, success: bool, say: str, reading: Reading | None = None, **extra: Any) -> dict[str, Any]:
        if self.simulated:
            say = "Simulated: " + say
        answer: dict[str, Any] = {"success": success, "say": say}
        if reading is not None:
            answer["state"] = reading.state
            answer["battery"] = reading.battery
        answer.update(extra)
        return answer

    async def _within(self, deadline: float, call: Awaitable[T]) -> T:
        try:
            return await asyncio.wait_for(call, timeout=max(0.0, deadline - self._clock()))
        except TimeoutError:
            raise Unavailable("The Dreame cloud is taking too long to answer.") from None

    async def _read(self, deadline: float) -> Reading:
        client = await self.link.ready(deadline)
        status = await self._within(deadline, client.get_status())
        if status.error or not status.raw:
            logger.warning("status read failed: %s", status.error)
            self.link.mark_stale()
            raise Unavailable(UNREACHABLE)
        return Reading.of(status)

    async def _send(self, cmd: str, deadline: float) -> None:
        client = await self.link.ready(deadline)
        result = await self._within(deadline, client.control(cmd))
        if not result.get("success"):
            logger.warning("%s not accepted: %s", cmd, result.get("error"))
            raise Unavailable("The Dreame cloud didn't take the command. Try again in a moment.")

    async def _after(self, deadline: float) -> Reading | None:
        """The state a moment after a command, or None when it cannot be read in time."""
        wait = min(self._settle, max(0.0, deadline - self._clock() - 4.0))
        await asyncio.sleep(wait)
        try:
            return await self._read(deadline)
        except Unavailable:
            return None

    async def _guarded(self, act: Callable[[float], Awaitable[dict[str, Any]]], sent: bool) -> dict[str, Any]:
        deadline = self._clock() + self._budget
        try:
            return await act(deadline)
        except Unavailable as e:
            return self._answer(False, e.say + (NOTHING_SENT if sent else ""))

    # -- tools ---------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            return self._answer(True, f"The vacuum is {r.words()}.", r)

        return await self._guarded(act, sent=False)

    async def start(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            if r.cleaning:
                return self._answer(True, f"It's already {r.words()}.", r)
            if self.ask_first:
                token = secrets.token_hex(3)
                self._question = (token, self._clock() + QUESTION_TTL)
                return self._answer(
                    True,
                    f"The vacuum is {r.words()}. Start a full clean now?",
                    r,
                    question=True,
                    token=token,
                )
            return await self._start(deadline)

        return await self._guarded(act, sent=True)

    async def confirm(self, token: str) -> dict[str, Any]:
        question, self._question = self._question, None
        if question is None or not secrets.compare_digest(question[0], token) or self._clock() > question[1]:
            return self._answer(False, "There is no open question with that token, so I didn't start it.")
        return await self._guarded(self._start, sent=True)

    async def _start(self, deadline: float) -> dict[str, Any]:
        await self._send("start_clean", deadline)
        r = await self._after(deadline)
        if r is None:
            return self._answer(True, "I sent the start. I couldn't check yet whether it set off.")
        if r.cleaning:
            return self._answer(True, f"Started. The vacuum is {r.words()}.", r)
        return self._answer(True, f"I sent the start. The vacuum says it is {r.words()}.", r)

    async def pause(self) -> dict[str, Any]:
        return await self._simple("pause", "Paused.", lambda r: r.stoppable, "nothing to pause")

    async def stop(self) -> dict[str, Any]:
        return await self._simple("stop", "Stopped.", lambda r: r.stoppable, "nothing to stop")

    async def go_home(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            if r.docked:
                return self._answer(True, f"It's already home: {r.words()}.", r)
            await self._send("go_home", deadline)
            after = await self._after(deadline)
            if after is None:
                return self._answer(True, "I sent it home. I couldn't check yet whether it set off.")
            return self._answer(True, f"Sent home. The vacuum is {after.words()}.", after)

        return await self._guarded(act, sent=True)

    async def find(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            await self._send("find_robot", deadline)
            return self._answer(True, "The vacuum is beeping now, so you can find it.")

        return await self._guarded(act, sent=True)

    async def _map(self, deadline: float) -> dict[str, Any]:
        """The decoded map (rooms, the robot's room), or Unavailable."""
        client = await self.link.ready(deadline)
        try:
            m = await self._within(deadline, client.get_map())
        except Unavailable:
            raise Unavailable("The map is taking too long to come from the Dreame cloud.") from None
        if not m.get("success") or not isinstance(m.get("map_data"), dict):
            logger.warning("map fetch failed: %s", m.get("error"))
            raise Unavailable("I can't get the vacuum's map right now.")
        return m

    async def where(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            try:
                m = await self._map(deadline)
            except Unavailable as e:
                return self._answer(False, f"{e.say} The vacuum is {r.words()}.", r)
            md = m["map_data"]
            rooms = md.get("room_list") or []
            room = md.get("robot_room")
            name = room["name"] if room else None
            if m.get("source") != "live":
                say = f"I only have its saved map, not where it is now. The vacuum is {r.words()}."
            elif not rooms:
                say = (
                    "It's on a map without any rooms, so probably somewhere it hasn't mapped before. "
                    f"The vacuum is {r.words()}."
                )
            elif r.docked or md.get("docked"):
                parts = [f"It's on its dock in {name}" if name else "It's on its dock"]
                if r.charging == CHARGING:
                    parts.append("charging")
                elif r.charging == CHARGED or r.code == 13:
                    parts.append("fully charged")
                if r.battery is not None:
                    parts.append(f"battery {r.battery} percent")
                say = ", ".join(parts) + "."
            elif name:
                say = f"It's in {name}, {r.words()}."
            else:
                say = f"It's on its map but not inside any room. The vacuum is {r.words()}."
            return self._answer(True, say, r, room=name)

        return await self._guarded(act, sent=False)

    async def rooms(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            m = await self._map(deadline)
            names = [room["name"] for room in m["map_data"].get("room_list") or []]
            if not names:
                return self._answer(True, "Its map has no rooms yet.", rooms=[])
            listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
            count = "one room" if len(names) == 1 else f"{len(names)} rooms"
            return self._answer(True, f"The map has {count}: {listed}.", rooms=names)

        return await self._guarded(act, sent=False)

    async def _simple(self, cmd: str, done: str, applies: Callable[[Reading], bool], nothing: str) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            if not applies(r):
                return self._answer(True, f"There's {nothing}: the vacuum is {r.words()}.", r)
            await self._send(cmd, deadline)
            after = await self._after(deadline)
            if after is None:
                return self._answer(True, f"{done} I couldn't check the state afterwards.")
            return self._answer(True, f"{done} The vacuum is {after.words()}.", after)

        return await self._guarded(act, sent=True)


def instructions(ask_first: bool, simulated: bool) -> str:
    text = (
        "Controls the user's robot vacuum through the Dreame cloud. Every answer has 'say': read it "
        "aloud as it is. vacuum_status tells what it is doing and its battery. vacuum_start starts a "
        "full clean, vacuum_pause pauses it, vacuum_stop stops it where it is, vacuum_go_home sends it "
        "back to the dock, vacuum_find makes it beep. vacuum_where says which room it is in, from its "
        "map; vacuum_rooms names the rooms on its map. Call a command only when the user asks for it "
        "now; never start a clean on your own. When 'success' is false, read 'say' and do not retry "
        "by yourself."
    )
    if ask_first:
        text += (
            " vacuum_start asks first: read its question aloud and call vacuum_confirm with its token "
            "only if the user clearly says yes. If they say anything else, do not confirm."
        )
    return (SIMULATED + text) if simulated else text


def build_server(vacuum: Vacuum) -> FastMCP:
    """The MCP server around one Vacuum; logs in when it starts, logs out when it stops."""

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        vacuum.link.start()
        try:
            yield
        finally:
            vacuum.link.close()

    mcp = FastMCP(
        "dreame-vacuum-voice",
        instructions=instructions(vacuum.ask_first, vacuum.simulated),
        lifespan=lifespan,
    )
    read_only = {"readOnlyHint": True, "openWorldHint": True}
    acts = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

    @mcp.tool(annotations=read_only)
    async def vacuum_status() -> dict[str, Any]:
        """What the vacuum is doing and how full its battery is."""
        return await vacuum.status()

    start_doc = (
        "Ask whether to start a full clean; start only with vacuum_confirm after the user's yes."
        if vacuum.ask_first
        else "Start a full clean of the home."
    )

    @mcp.tool(annotations=acts, description=start_doc)
    async def vacuum_start() -> dict[str, Any]:
        return await vacuum.start()

    if vacuum.ask_first:

        @mcp.tool(annotations=acts)
        async def vacuum_confirm(token: str) -> dict[str, Any]:
            """Start the clean the user just said yes to, with the token from vacuum_start's question."""
            return await vacuum.confirm(token)

    @mcp.tool(annotations=acts)
    async def vacuum_pause() -> dict[str, Any]:
        """Pause the clean; vacuum_start carries on from there."""
        return await vacuum.pause()

    @mcp.tool(annotations=acts)
    async def vacuum_stop() -> dict[str, Any]:
        """Stop the clean where the vacuum is."""
        return await vacuum.stop()

    @mcp.tool(annotations=acts)
    async def vacuum_go_home() -> dict[str, Any]:
        """Send the vacuum back to its dock."""
        return await vacuum.go_home()

    @mcp.tool(annotations=acts)
    async def vacuum_find() -> dict[str, Any]:
        """Make the vacuum beep, to find it."""
        return await vacuum.find()

    @mcp.tool(annotations=read_only)
    async def vacuum_where() -> dict[str, Any]:
        """Which room the vacuum is in, from its live map, and whether it is on its dock."""
        return await vacuum.where()

    @mcp.tool(annotations=read_only)
    async def vacuum_rooms() -> dict[str, Any]:
        """The names of the rooms on the vacuum's map."""
        return await vacuum.rooms()

    return mcp


class _QuietDiscover(logging.Filter):
    """Drop the mcp library's long warning for a server/discover request.

    Newer MCP clients ask server/discover first; this mcp version does not
    know it, answers with an error, the client falls back, and the library
    logs a 30-line validation warning to the root logger every time.
    Other validation warnings still get through.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (message.startswith("Failed to validate request") and "server/discover" in message)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Voice-sized MCP server (stdio) for a Dreame robot vacuum")
    p.add_argument("--simulate", action="store_true", help="a pretend vacuum: no login, no cloud")
    p.add_argument(
        "--secrets-dir",
        help="folder with files 'user' and 'password' (DreameHome login); ~ is expanded",
    )
    p.add_argument(
        "--ask-before-start",
        action="store_true",
        help="vacuum_start asks first; vacuum_confirm starts the clean",
    )
    args = p.parse_args(argv)

    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger().addFilter(_QuietDiscover())

    if args.simulate:
        from .simulator import SimulatedVacuum

        client: VacuumClient | None = SimulatedVacuum()
    else:
        client = client_from_env(args.secrets_dir)

    vacuum = Vacuum(Link(client), ask_first=args.ask_before_start, simulated=args.simulate)
    build_server(vacuum).run(transport="stdio", show_banner=False)


if __name__ == "__main__":
    main()
