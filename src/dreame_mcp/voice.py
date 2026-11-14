"""A voice-sized MCP server for a Dreame robot vacuum: nine small tools with spoken answers.

The main server (``server.py``) is a full workbench: one portmanteau tool, a
map, a web app, sampling. A voice assistant needs much less and needs it
short: is the vacuum busy, how full is its battery, start, pause, stop, go
home, beep, where is it, which rooms, clean these rooms. This module serves exactly that over stdio, for voice bridges and
other clients that read a one-line answer aloud.

Every tool answers ``{"success", "say", "state", "battery"}``. ``say`` is one
or two sentences to read aloud as they are. When the cloud cannot be reached,
``success`` is false and ``say`` says so, that nothing was sent, and what to
do next: every answer that is not a success ends with something to do.

Run it:

    dreame-voice-mcp --secrets-dir ~/.config/dreame   # login read from files
    dreame-voice-mcp                                  # login from env / .env
    dreame-voice-mcp --simulate                       # a pretend vacuum

``--ask-before-start`` makes ``vacuum_clean_all`` ask first and adds
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
from .login_wait import LoginWait, reason_of

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
CHECK_FIRST = "Ask me for its status before you try again."
UNREACHABLE = "I can't reach the Dreame cloud right now."
TRY_AGAIN = "Try again in a minute."
RESTART = "then ask me to restart the vacuum plugin."
#: The login is typed into the manager's masked boxes, so it never touches a terminal or a chat.
LOGIN_BOXES = "Ask the claude-voice manager to open the DreameHome login boxes, then to restart the vacuum plugin."
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
    async def clean_rooms(self, room_ids: list[int]) -> dict: ...
    async def get_full_status(self) -> dict: ...
    def disconnect(self) -> None: ...


def _listed(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C'."""
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _room_key(name: str) -> str:
    """A room name as compared: case and spacing ignored, and a leading 'the'."""
    words = name.casefold().split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def _pick_rooms(asked: list[str], room_list: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str | None]:
    """The map's rooms the user named, each once and in their order, or the spoken reason there are none."""
    existing = _listed([room["name"] for room in room_list])
    if not asked:
        return [], f"Which rooms? Say one or more of {existing}."
    known = {_room_key(room["name"]): room for room in room_list}
    unknown = [name.strip() for name in asked if _room_key(name) not in known]
    # RULE: an unknown room is refused
    if unknown:
        these = "There is no room called " + _listed([f"'{u}'" for u in unknown])
        return [], f"{these}. Choose from {existing}."
    chosen: list[dict[str, Any]] = []
    for name in asked:
        room = known[_room_key(name)]
        if room not in chosen:
            chosen.append(room)
    return chosen, None


class Unavailable(Exception):
    """The vacuum cannot be reached: what is wrong and what the owner can do next, both spoken."""

    def __init__(self, wrong: str, then: str, maybe_sent: bool = False):
        super().__init__(f"{wrong} {then}")
        self.wrong = wrong
        self.then = then
        self.maybe_sent = maybe_sent  # the command was handed over: it may have reached the robot

    @property
    def say(self) -> str:
        return f"{self.wrong} {self.then}"


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


# -- the full status ------------------------------------------------------------
# Codes as the robot reports them (client.FULL_STATUS says where each comes from), in
# words for 'say'. A code with no words here is said by its number.

#: Error (2.2) and warning (4.35) codes, from the reference's DreameVacuumErrorCode.
PROBLEMS: dict[int, str] = {
    1: "a wheel has dropped",
    2: "a cliff sensor is blocked",
    3: "its bumper is stuck",
    8: "the dust bin is out",
    9: "the water tank is out",
    10: "the water tank is empty",
    11: "the dust bin is full",
    12: "the main brush is tangled",
    13: "the side brush is tangled",
    20: "its battery is too low",
    47: "it is blocked",
    51: "the filter is blocked",
    68: "the mop should come off",
    80: "it is stuck",
    81: "it is stuck",
    101: "the dust bin is full",
    102: "the dust bin is out",
    103: "the dust bin is out",
    104: "the dust bin is full",
    105: "the water tank is out",
    107: "the water tank is empty",
    121: "the dust bag in the base is full",
}
#: Wear readings: (name in the answer, words, percent left at siid.piid, hours left at).
PARTS = (
    ("main_brush", "main brush", "9.2", "9.1"),
    ("side_brush", "side brush", "10.2", "10.1"),
    ("filter", "filter", "11.1", "11.2"),
    ("mop_pad", "mop pad", "18.1", "18.2"),
    ("sensors", "sensors", "16.1", "16.2"),
)
#: A part with this many percent left, or fewer, is due.
DUE = 10
#: What the cloud gives no reading for, said at the end of every full status.
NOT_REPORTED = ("water level", "bin fill", "base water tanks")
# Said on any robot, so it names no model: the base's water tanks (27.1, 27.2) do answer, but
# an L10s Plus base has none, so they are not read rather than read out wrong.
CANNOT_REPORT = "It can't report the water level or how full the bin is, and it doesn't read the base's water tanks."
#: During a clean the cloud also gives no rooms done (the map has no flag for it, and no order
#: on a full clean) and no time left (no property answers with it); read on 2026-11-14.
NOT_REPORTED_CLEANING = ("rooms done", "time left")
CANNOT_REPORT_CLEANING = (
    "It can't report which rooms are done or how long is left, the water level or how full the bin is,"
    " and it doesn't read the base's water tanks."
)
WATER_TANK = {0: "out", 1: "fitted", 10: "fitted", 99: "in the station"}
WATER_FLOW = {1: "low", 2: "medium", 3: "high"}
LOW_WATER = {1: "the water tank is empty", 2: "the water tank is empty", 3: "the water tank is empty",
             4: "the water tank is empty", 5: "the water tank is low", 6: "the water tank is out"}  # fmt: skip
DUST_BAG = {0: "fitted", 1: "not fitted", 2: "to be checked"}


def _num(raw: dict[str, Any], key: str) -> int | None:
    try:
        return int(raw[key])
    except (KeyError, TypeError, ValueError):
        return None


def _problem(code: int) -> str:
    return PROBLEMS.get(code, f"error code {code}, which the DreameHome app explains")


def _sentence(parts: list[str]) -> str:
    return parts[0][0].upper() + parts[0][1:] + "".join(f"; {p}" for p in parts[1:]) + "."


def _known(table: dict[int, str], code: int | None) -> str | None:
    """A code's words, or None when the cloud left the reading out or has no words for it."""
    return None if code is None else table.get(code)


def _wear(raw: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Percent and hours left of each part the cloud reported; a reading it left out stays out."""
    wear: dict[str, dict[str, int]] = {}
    for name, _, percent_at, hours_at in PARTS:
        left = {"percent": _num(raw, percent_at), "hours": _num(raw, hours_at)}
        if left["percent"] is not None:
            wear[name] = {k: v for k, v in left.items() if v is not None}
    return wear


@dataclass(frozen=True)
class FullStatus:
    """A full status read, in words; None for a reading the cloud left out, never a guess."""

    raw: dict[str, Any]
    water_tank: str | None
    mop_pad: str | None
    water_flow: str | None
    auto_empty: bool | None
    dust_bag: str | None
    wear: dict[str, dict[str, int]]

    @classmethod
    def of(cls, raw: dict[str, Any]) -> FullStatus:
        tank_code, mop, auto = _num(raw, "4.6"), _num(raw, "4.53"), _num(raw, "15.1")
        # A tank reported as fitted with its mop (10) has the mop on, whatever 4.53 says.
        with_mop = mop if mop is None or tank_code != 10 else 1
        return cls(
            raw=raw,
            water_tank=_known(WATER_TANK, tank_code),
            mop_pad=_known({0: "not attached", 1: "attached"}, with_mop),
            water_flow=_known(WATER_FLOW, _num(raw, "4.5")),
            auto_empty=None if auto is None else bool(auto),
            dust_bag=_known(DUST_BAG, _num(raw, "27.3")),
            wear=_wear(raw),
        )

    def problems(self) -> list[str]:
        found = [_problem(code) for key in ("2.2", "4.35") if (code := _num(self.raw, key))]
        # RULE: a missing water tank is a problem
        if self.water_tank == "out":
            found.append("the water tank is out")
        found += [low] if (low := _known(LOW_WATER, _num(self.raw, "4.41"))) else []
        if self.dust_bag in ("not fitted", "to be checked"):
            found.append(f"the dust bag in the base is {self.dust_bag}")
        if _num(self.raw, "15.5") == 2:
            found.append("the base didn't empty the bin")
        found += [_due(name, words) for name, words, _, _ in PARTS if self._worn(name)]
        return list(dict.fromkeys(found))  # the same problem from two readings is said once

    def _worn(self, name: str) -> bool:
        percent = self.wear.get(name, {}).get("percent", 100)  # not reported: not due
        # RULE: a part at ten percent or less is due
        return percent <= DUE

    def words(self, reading: Reading) -> list[str]:
        robot = [f"{what} {value}" for what, value in
                 (("water tank", self.water_tank), ("mop pad", self.mop_pad), ("water flow", self.water_flow)) if value]  # fmt: skip
        base = [] if self.auto_empty is None else ["auto-empty " + ("on" if self.auto_empty else "off")]
        base += [f"dust bag {self.dust_bag}"] if self.dust_bag else []
        worn = [f"{words} {self.wear[name]['percent']}" for name, words, _, _ in PARTS if name in self.wear]
        parts = [f"it is {reading.words()}", ", ".join(robot)]
        parts += ["left: " + ", ".join(worn) + " percent"] if worn else []
        return [p for p in (*parts, ", ".join(base)) if p]


#: How far a clean has got, in the order said: (field, unit words).
PROGRESS = (("square_metres", "square metres"), ("minutes", "minutes"))


def _progress(raw: dict[str, Any]) -> dict[str, int] | None:
    """How far this clean has got, from what the robot reports; None when it reports none of it."""
    got = {"percent": _num(raw, "4.63"), "square_metres": _num(raw, "4.3"), "minutes": _num(raw, "4.2")}
    return {k: v for k, v in got.items() if v is not None} or None


def _progress_words(progress: dict[str, int], room: str | None) -> list[str]:
    """'40 percent done, 21 square metres in 25 minutes', then 'now in Room 2'."""
    done = f"{progress['percent']} percent done" if "percent" in progress else ""
    so_far = " in ".join(f"{progress[k]} {unit}" for k, unit in PROGRESS if k in progress)
    words = [", ".join(w for w in (done, so_far) if w), f"now in {room}" if room else ""]
    return [w for w in words if w]


def _due(name: str, words: str) -> str:
    return "the sensors are due for cleaning" if name == "sensors" else f"the {words} is due for a change"


def full_status(raw: dict[str, Any], room: str | None = None) -> tuple[str, dict[str, Any]]:
    """What a full status says, and its fields, from the robot's readings and, during a
    clean, the room the live map puts it in. A reading the cloud left out is not guessed:
    it is left out of both."""
    full, reading = FullStatus.of(raw), Reading.of(DreameStatus(raw=raw))
    problems = full.problems()
    words = full.words(reading)
    progress = _progress(raw) if reading.cleaning else None
    if progress or (reading.cleaning and room):
        # RULE: a clean in progress says how far it has got
        words[1:1] = _progress_words(progress or {}, room)
    # RULE: a full status says problems first
    opening = "Problems: " + "; ".join(problems) + "." if problems else "No problems."
    # RULE: a full status says what it cannot report
    say = f"{opening} {_sentence(words)} {CANNOT_REPORT_CLEANING if reading.cleaning else CANNOT_REPORT}"
    fields: dict[str, Any] = {
        "problems": problems,
        "docked": reading.docked,
        "water_tank": full.water_tank,
        "mop_pad": full.mop_pad,
        "water_flow": full.water_flow,
        "wear": full.wear,
        "auto_empty": full.auto_empty,
        "dust_bag": full.dust_bag,
        "not_reported": [*NOT_REPORTED, *(NOT_REPORTED_CLEANING if reading.cleaning else ())],
        "progress": progress,
        "room": room if reading.cleaning else None,
    }
    return say, fields


def _login_problem(cloud_error: str | None) -> tuple[str, str]:
    """A spoken reason for a failed login and what to do about it, never quoting the account."""
    text = (cloud_error or "").lower()
    if "70016" in text or "credentials" in text:
        return (
            "The Dreame cloud turned down the login.",
            LOGIN_BOXES,
        )
    if "2fa" in text or "verification" in text or "captcha" in text:
        return (
            "The Dreame cloud wants an extra check before it lets me in.",
            "Open the DreameHome app, then ask again in a minute.",
        )
    if "rate-limited" in text:
        return (
            "The Dreame cloud has had too many logins and wants me to wait.",
            "Ask again in a few minutes, not sooner.",
        )
    return UNREACHABLE, TRY_AGAIN


class Link:
    """Keeps the client logged in: in the background at start, again when it has dropped.

    Retries a failed login only after ``retry_after`` seconds, so a string of
    voice requests cannot hammer the cloud into asking for a captcha. A failed cloud
    login also waits in ``wait``, shared with the web page through a small file
    (login_wait), so neither process logs in again right after the other failed.
    """

    def __init__(
        self,
        client: VacuumClient | None,
        *,
        retry_after: float = RETRY_AFTER,
        clock: Callable[[], float] = time.monotonic,
        wait: LoginWait | None = None,
    ):
        self.client = client
        self._retry_after = retry_after
        self._clock = clock
        # Wall time as this Link's clock sees it pass, so a test's clock moves the shared
        # wait too; with the real monotonic clock it is time.time().
        started, wall = clock(), time.time()
        self._wait = wait or LoginWait(clock=lambda: wall + (self._clock() - started))
        self._task: asyncio.Task[bool] | None = None
        self._last_try: float | None = None
        self._suspect = False  # a call failed while logged in: the session may have run out

    def start(self) -> None:
        """Begin logging in without waiting for it."""
        if self.client is not None and self._task is None:
            self._begin()

    def _begin(self) -> None:
        assert self.client is not None
        self._suspect = False
        self._last_try = self._clock()
        self._task = asyncio.ensure_future(self._connect(self.client))

    async def _connect(self, client: VacuumClient) -> bool:
        try:
            ok = await client.connect()
        except Exception:
            logger.exception("connect failed")
            return False
        if client.cloud_error:
            self._wait.failed(reason_of(client.cloud_error))
        elif ok and client.connected:
            self._wait.succeeded()
        return ok

    def mark_stale(self) -> None:
        """A call failed while logged in: log in again on a later call, once the wait is over.

        The session may have run out: the library renews it only shortly before it ends,
        and only when a request comes in then, so a quiet night can let it lapse. Until
        the wait is over the session is used as it is."""
        if self._task is not None and self._task.done():
            self._suspect = True

    def _drop_if_stale(self, client: VacuumClient) -> None:
        """Forget a finished login the cloud has dropped, or one a failed call put in doubt
        once the wait since the last login is over."""
        if self._task is None or not self._task.done():
            return
        waited = self._clock() - (self._last_try or float("-inf"))
        # RULE: a session the cloud called invalid logs in again at once
        if not client.connected or (self._suspect and waited >= self._retry_after):
            self._task = None

    async def ready(self, deadline: float) -> VacuumClient:
        """The logged-in client, or Unavailable with the spoken reason."""
        client = self.client
        if client is None:
            raise Unavailable(
                "The vacuum isn't set up on the computer yet: its Dreame login is missing.",
                LOGIN_BOXES,
            )
        self._drop_if_stale(client)
        if self._task is None:
            waited = self._clock() - (self._last_try or float("-inf"))
            # RULE: a failed login waits before trying again
            if waited < self._retry_after and self._last_try is not None:
                raise Unavailable(*_login_problem(client.cloud_error))
            # RULE: a failed login in either process makes the other wait
            if self._wait.left() > 0:
                if client.cloud_error:
                    raise Unavailable(*_login_problem(client.cloud_error))
                raise Unavailable(
                    "A Dreame login failed a moment ago, so I'm waiting before I try again.",
                    "Ask again in a minute.",
                )
            self._begin()
        assert self._task is not None
        try:
            # RULE: every tool answers within the voices time
            ok = await asyncio.wait_for(asyncio.shield(self._task), timeout=max(0.0, deadline - self._clock()))
        except TimeoutError:
            raise Unavailable("I'm still logging in to the Dreame cloud.", "Ask again in a moment.") from None
        if not ok or not client.connected:
            self._task = None  # a later call may try again, after the wait
            raise Unavailable(*_login_problem(client.cloud_error))
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
        # The open question: its token, when it expires, and whether a confirm was already tried.
        self._question: tuple[str, float, bool] | None = None

    # -- answers -------------------------------------------------------

    def _answer(self, success: bool, say: str, reading: Reading | None = None, **extra: Any) -> dict[str, Any]:
        if self.simulated:
            # RULE: a simulated answer says so
            say = "Simulated: " + say
        answer: dict[str, Any] = {"success": success, "say": say}
        if reading is not None:
            answer["state"] = reading.state
            answer["battery"] = reading.battery
        answer.update(extra)
        return answer

    async def _within(self, deadline: float, call: Awaitable[T]) -> T:
        try:
            # RULE: a slow cloud still answers within the budget
            return await asyncio.wait_for(call, timeout=max(0.0, deadline - self._clock()))
        except TimeoutError:
            raise Unavailable("The Dreame cloud is taking too long to answer.", "Try again in a moment.") from None

    async def _read(self, deadline: float) -> Reading:
        client = await self.link.ready(deadline)
        status = await self._within(deadline, client.get_status())
        if status.error or not status.raw:
            logger.warning("status read failed: %s", status.error)
            self.link.mark_stale()
            raise Unavailable(UNREACHABLE, TRY_AGAIN)
        return Reading.of(status)

    async def _send(self, cmd: str, deadline: float) -> None:
        client = await self.link.ready(deadline)
        await self._handed_over(cmd, deadline, client.control(cmd))

    async def _handed_over(self, what: str, deadline: float, call: Awaitable[dict[str, Any]]) -> None:
        """Wait for a command's answer. Once it is handed to the client it may reach the
        robot even when no answer comes back, so a failure from here on never says it
        wasn't sent (the client's worker thread carries on after the wait ends)."""
        try:
            result = await self._within(deadline, call)
        except Unavailable:
            raise Unavailable(
                "The Dreame cloud took too long to answer, so the command may still reach the vacuum.",
                CHECK_FIRST,
                maybe_sent=True,
            ) from None
        if not result.get("success"):
            logger.warning("%s not confirmed: %s", what, result.get("error"))
            raise Unavailable(
                "The Dreame cloud didn't confirm the command, so it may have reached the vacuum or not.",
                CHECK_FIRST,
                maybe_sent=True,
            )

    async def _after(self, deadline: float) -> Reading | None:
        """The state a moment after a command, or None when it cannot be read in time."""
        wait = min(self._settle, max(0.0, deadline - self._clock() - 4.0))
        await asyncio.sleep(wait)
        try:
            # RULE: an action says the state read back after it
            return await self._read(deadline)
        except Unavailable:
            return None

    async def _guarded(self, act: Callable[[float], Awaitable[dict[str, Any]]], sent: bool) -> dict[str, Any]:
        deadline = self._clock() + self._budget
        try:
            return await act(deadline)
        except Unavailable as e:
            # A failed command or map, not only a failed read, may mean the session ran out.
            # RULE: a failed command logs in again on the next call
            self.link.mark_stale()
            # RULE: a command that may have gone says so
            sent = sent and not e.maybe_sent
            # RULE: a command that was not sent says so
            # RULE: every failure says what to do next
            return self._answer(False, e.wrong + (NOTHING_SENT if sent else "") + " " + e.then)

    # -- tools ---------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            return self._answer(True, f"The vacuum is {r.words()}.", r)

        return await self._guarded(act, sent=False)

    async def _room_now(self, deadline: float) -> str | None:
        """The room the live map puts it in, or None: a saved map, or none in time, names no room."""
        try:
            m = await self._map(deadline)
        except Unavailable:
            return None
        room = m["map_data"].get("robot_room") or {}
        # RULE: a full status names the room only from the live map
        return room.get("name") if m.get("source") == "live" else None

    async def full_status(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            client = await self.link.ready(deadline)
            got = await self._within(deadline, client.get_full_status())
            if not got.get("success") or not got.get("raw"):
                logger.warning("full status read failed: %s", got.get("error"))
                self.link.mark_stale()
                raise Unavailable(UNREACHABLE, TRY_AGAIN)
            reading = Reading.of(DreameStatus(raw=got["raw"]))
            room = await self._room_now(deadline - 1.0) if reading.cleaning else None
            say, fields = full_status(got["raw"], room)
            return self._answer(True, say, reading, **fields)

        return await self._guarded(act, sent=False)

    async def start(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            if r.cleaning:
                return self._answer(True, f"It's already {r.words()}.", r)
            if self.ask_first:
                token = secrets.token_hex(3)
                self._question = (token, self._clock() + QUESTION_TTL, False)
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
        # RULE: a confirm needs the open questions token
        # RULE: a question expires
        if question is None or not secrets.compare_digest(question[0], token) or self._clock() > question[1]:
            return self._answer(
                False,
                "There is no open question with that token, so I didn't start it. "
                "Ask for a full clean again if you still want one.",
            )
        _, expires, tried = question

        async def act(deadline: float) -> dict[str, Any]:
            # RULE: a retried confirm does not start twice
            if tried:
                # The last try may have reached the robot without its answer reaching us.
                r = await self._read(deadline)
                if r.cleaning:
                    return self._answer(True, f"It's already {r.words()}.", r)
            return await self._start(deadline)

        answer = await self._guarded(act, sent=True)
        if not answer["success"] and self._question is None:
            # A failed start keeps the token, so the same yes can be tried again until it
            # expires. What keeps that safe when the start may have gone is the tried
            # flag (True here): the retry reads the state first and does not start twice.
            # RULE: a token is spent only once the start was sent
            self._question = (token, expires, True)
        return answer

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
            raise Unavailable(
                "The map is taking too long to come from the Dreame cloud.", "Try again in a moment."
            ) from None
        if not m.get("success") or not isinstance(m.get("map_data"), dict):
            logger.warning("map fetch failed: %s", m.get("error"))
            raise Unavailable("I can't get the vacuum's map right now.", TRY_AGAIN)
        return m

    async def where(self) -> dict[str, Any]:
        async def act(deadline: float) -> dict[str, Any]:
            r = await self._read(deadline)
            try:
                m = await self._map(deadline)
            except Unavailable as e:
                return self._answer(False, f"{e.wrong} The vacuum is {r.words()}. " + e.then, r)
            md = m["map_data"]
            rooms = md.get("room_list") or []
            room = md.get("robot_room")
            name = room["name"] if room else None
            # RULE: a saved map does not claim where the robot is
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
            count = "one room" if len(names) == 1 else f"{len(names)} rooms"
            return self._answer(True, f"The map has {count}: {_listed(names)}.", rooms=names)

        return await self._guarded(act, sent=False)

    async def clean_rooms(self, rooms: list[str]) -> dict[str, Any]:
        """Clean the named rooms, in the order given; names as vacuum_rooms gives them."""

        async def act(deadline: float) -> dict[str, Any]:
            asked = [r for r in (rooms or []) if r and r.strip()]
            r = await self._read(deadline)
            if r.cleaning:
                # RULE: a room clean while cleaning is refused
                return self._answer(False, f"It's already {r.words()}. Stop it first, then ask again.", r, rooms=[])
            m = await self._map(deadline)
            room_list = m["map_data"].get("room_list") or []
            if not room_list:
                return self._answer(
                    False,
                    "Its map has no rooms, so I can't clean by room. "
                    "Use the DreameHome app to split the map into rooms, or ask for a full clean.",
                    r,
                    rooms=[],
                )
            chosen, refusal = _pick_rooms(asked, room_list)
            if refusal:
                return self._answer(False, refusal, r, rooms=[])
            names = [room["name"] for room in chosen]
            client = await self.link.ready(deadline)
            await self._handed_over("room clean", deadline, client.clean_rooms([int(room["id"]) for room in chosen]))
            return self._set_off(await self._after(deadline), names)

        return await self._guarded(act, sent=True)

    def _set_off(self, after: Reading | None, names: list[str]) -> dict[str, Any]:
        """The answer once a room clean was sent, from the state read back after it."""
        if after is None:
            return self._answer(
                True, f"I sent it to clean {_listed(names)}. I couldn't check yet whether it set off.", rooms=names
            )
        if after.cleaning:
            say = f"Started cleaning {_listed(names)}, battery {after.battery} percent."
            if after.battery is None:
                say = f"Started cleaning {_listed(names)}."
            return self._answer(True, say, after, rooms=names)
        return self._answer(
            True, f"I sent it to clean {_listed(names)}. The vacuum says it is {after.words()}.", after, rooms=names
        )

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
        "aloud as it is. vacuum_status tells what it is doing and its battery. vacuum_clean_all starts a "
        "full clean, vacuum_pause pauses it, vacuum_stop stops it where it is, vacuum_go_home sends it "
        "back to the dock, vacuum_find makes it beep. vacuum_where says which room it is in, from its "
        "map; vacuum_rooms names the rooms on its map; vacuum_clean_rooms cleans the rooms the user names, "
        "by the names vacuum_rooms gives. Call a command only when the user asks for it "
        "now; never start a clean on your own. When 'success' is false, read 'say' and do not retry "
        "by yourself."
    )
    if ask_first:
        text += (
            " vacuum_clean_all asks first: read its question aloud and call vacuum_confirm with its token "
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
    # RULE: a tool that changes nothing is marked read only
    read_only = {"readOnlyHint": True, "openWorldHint": True}
    acts = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

    @mcp.tool(annotations=read_only)
    async def vacuum_status() -> dict[str, Any]:
        """State and battery now.

        What the vacuum is doing and how full its battery is.
        """
        return await vacuum.status()

    @mcp.tool(annotations=read_only)
    async def vacuum_full_status() -> dict[str, Any]:
        """Everything it can report.

        Problems first (errors, warnings, a missing water tank, worn parts), then the water
        tank, mop pad, wear of brushes, filter and mop, and the base; and what it can't
        report. Only when the user asks for the full status; vacuum_status is the short one.
        """
        return await vacuum.full_status()

    start_doc = (
        "Ask, then clean it all.\n\n"
        "Asks whether to start a full clean of the home;"
        " start only with vacuum_confirm after the user's yes."
        if vacuum.ask_first
        else "Full clean of the home.\n\nStarts cleaning every room."
    )

    @mcp.tool(annotations=acts, description=start_doc)
    async def vacuum_clean_all() -> dict[str, Any]:
        return await vacuum.start()

    if vacuum.ask_first:

        @mcp.tool(annotations=acts)
        async def vacuum_confirm(token: str) -> dict[str, Any]:
            """Yes to the asked clean.

            Start the clean the user just said yes to, with the token from vacuum_clean_all's question.
            """
            return await vacuum.confirm(token)

    @mcp.tool(annotations=acts)
    async def vacuum_pause() -> dict[str, Any]:
        """Pause the clean.

        vacuum_clean_all carries on from there.
        """
        return await vacuum.pause()

    @mcp.tool(annotations=acts)
    async def vacuum_stop() -> dict[str, Any]:
        """Stop where it is.

        Ends the clean and leaves the vacuum where it stands.
        """
        return await vacuum.stop()

    @mcp.tool(annotations=acts)
    async def vacuum_go_home() -> dict[str, Any]:
        """Back to the dock.

        Send the vacuum home to its dock.
        """
        return await vacuum.go_home()

    @mcp.tool(annotations=acts)
    async def vacuum_find() -> dict[str, Any]:
        """Beep, to find it.

        The vacuum plays a sound so the user can find it.
        """
        return await vacuum.find()

    @mcp.tool(annotations=read_only)
    async def vacuum_where() -> dict[str, Any]:
        """Which room it is in.

        From its live map, and whether it is on its dock.
        """
        return await vacuum.where()

    @mcp.tool(annotations=read_only)
    async def vacuum_rooms() -> dict[str, Any]:
        """List the map's rooms.

        The names of the rooms on the vacuum's map.
        """
        return await vacuum.rooms()

    @mcp.tool(annotations=acts)
    async def vacuum_clean_rooms(rooms: list[str]) -> dict[str, Any]:
        """Clean only named rooms.

        Cleans them in the order given. Use the room names vacuum_rooms gives, such as "Room 3".
        """
        return await vacuum.clean_rooms(rooms)

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
        help="vacuum_clean_all asks first; vacuum_confirm starts the clean",
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
