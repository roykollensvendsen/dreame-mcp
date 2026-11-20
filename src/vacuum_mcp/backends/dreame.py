"""The Dreame backend: Dreame's property codes in, neutral ``Readings`` out.

This is the only place in vacuum_mcp that knows Dreame's codes (siid.piid, state and
error numbers) and which Dreame model reports what. The numbers are the MIoT spec's for
a Dreame vacuum, the meanings the reference project's names for them (client.FULL_STATUS
says where each is read). Checked against the owner's L10s Plus (dreame.vacuum.r2363a).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..backend import LoginTrouble, LoginWait, VacuumClient
from ..capabilities import COMMANDS, Capabilities
from ..readings import FIELDS, Reading, Readings, Source

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

WATER_TANK = {0: "out", 1: "fitted", 10: "fitted", 99: "in the station"}
WATER_FLOW = {1: "low", 2: "medium", 3: "high"}
LOW_WATER = {1: "the water tank is empty", 2: "the water tank is empty", 3: "the water tank is empty",
             4: "the water tank is empty", 5: "the water tank is low", 6: "the water tank is out"}  # fmt: skip
DUST_BAG = {0: "fitted", 1: "not fitted", 2: "to be checked"}


# -- what each model can do, and which readings it is trusted on --------------------
#: Readings any Dreame vacuum gives the same way, from the MIoT spec.
_COMMON = frozenset({"state", "activity", "battery", "charging", "on_dock", "fully_charged", "error", "area", "time"})
#: Per model. Only this table knows a model's quirks.
MODELS: dict[str, Capabilities] = {
    # L10s Plus. Its mop is part of the water tank module, so 4.53 (inverted, and only
    # meaningful on models that drop their mop pad) is never read. 4.6 read 1 with the tank
    # and its mop fitted (18.11), so it gives the tank but not the mop: the mop stays
    # untrusted until a live 10 and 0 are seen with the mop known. Bin fill has no property.
    # Every command, start_rooms included: it is the one a model without room maps would lack.
    "dreame.vacuum.r2363a": Capabilities(commands=COMMANDS, readings=frozenset(FIELDS) - {"mop_pad", "dust_bin"}),
}
#: A Dreame model not in the table: the commands every Dreame takes, and only the common readings
#: (not water flow, the base's dust bag or the percent done, which only some models report).
#: start_rooms is the command to gate first if a model turns out to lack room maps.
OTHER = Capabilities(commands=COMMANDS, readings=_COMMON)


def capabilities(model: str | None) -> Capabilities:
    """What this model can do and which readings it is trusted on."""
    return MODELS.get(model or "", OTHER)


def known_model(model: str | None) -> bool:
    """True when the model has its own row, so more than the common readings are trusted."""
    return (model or "") in MODELS


# -- reading ------------------------------------------------------------------------


def _num(raw: dict[str, Any], key: str) -> int | None:
    try:
        return int(raw[key])
    except (KeyError, TypeError, ValueError):
        return None


def _state(code: int | None, charging: int | None) -> str | None:
    if code == 2 and charging in (CHARGING, CHARGED):
        return "charging" if charging == CHARGING else "docked"
    return STATES[code][0] if code in STATES else None


#: What an idle robot on its dock is doing, by its charging code.
ON_THE_DOCK = {CHARGING: "charging on the dock", CHARGED: "on the dock, fully charged"}


def _activity(code: int | None, charging: int | None, error: int | None) -> str | None:
    """What it is doing, in the words of this table, never the robot's own text."""
    if code is None:
        return None
    what = _state_words(code, charging)
    if code == 4 and error:
        what += f", error code {error}; the DreameHome app says what it means"
    return what


def _state_words(code: int, charging: int | None) -> str:
    if code == 2 and charging in ON_THE_DOCK:
        return ON_THE_DOCK[charging]
    if code in STATES:
        return STATES[code][1]
    return f"busy with something I have no name for (state {code})"


def _problem(code: int) -> str:
    return PROBLEMS.get(code, f"error code {code}, which the DreameHome app explains")


def _consumables(raw: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    wear: dict[str, dict[str, int]] = {}
    for name, _, percent_at, hours_at in PARTS:
        left = {"percent": _num(raw, percent_at), "hours": _num(raw, hours_at)}
        if left["percent"] is not None:
            wear[name] = {k: v for k, v in left.items() if v is not None}
    return wear or None


def _either(by_state: bool, by_charging: bool, code: int | None, charging: int | None) -> bool | None:
    """True when the state or the charging code says so; None when neither was read."""
    return None if code is None and charging is None else by_state or by_charging


def _looked_up[V](value: int | None, table: dict[int, V]) -> V | None:
    return None if value is None else table.get(value)


def _errors(raw: dict[str, Any], trusted: frozenset[str]) -> tuple[str, ...] | None:
    """Every problem this model can be trusted to name, in words; None when none was read."""
    # Only keys the model is trusted on count as a read: "no problems" needs a trusted read.
    # RULE: only readings the model is trusted on count as a problems read
    read_keys = {key for key, source in _PROBLEM_SOURCES.items() if key in raw and source in trusted}
    if "error" not in trusted or not read_keys:
        return None
    # A problem drawn from another reading is named only when the model trusts that reading.
    found = [_problem_words(key, _num(raw, key)) for key in _PROBLEM_SOURCES if key in read_keys]
    return tuple(dict.fromkeys(words for words in found if words))


def _problem_words(key: str, value: int | None) -> str | None:
    """The words for one problem key's value, or None when it names no problem."""
    if not value:
        return None
    named = {
        "2.2": lambda: _problem(value),
        "4.35": lambda: _problem(value),
        "4.41": lambda: LOW_WATER.get(value),
        "27.3": lambda: _bag_problem(DUST_BAG.get(value)),
        "15.5": lambda: "the base didn't empty the bin" if value == 2 else None,
    }
    return named[key]()


def _bag_problem(bag: str | None) -> str | None:
    return f"the dust bag in the base is {bag}" if bag in ("not fitted", "to be checked") else None


#: Where each problem key comes from: the reading a model must be trusted on to name it.
_PROBLEM_SOURCES = {"2.2": "error", "4.35": "error", "4.41": "water_tank", "27.3": "dust_bag", "15.5": "auto_empty"}


def read(raw: dict[str, Any], model: str | None, as_of: float, source: Source) -> Readings:
    """One read's properties ({"siid.piid": value}) as neutral readings for this model."""
    trusted = capabilities(model).readings
    code, charging, tank, auto = _num(raw, "2.1"), _num(raw, "3.2"), _num(raw, "4.6"), _num(raw, "15.1")
    values: dict[str, Any] = {
        "state": _state(code, charging),
        "activity": _activity(code, charging, _num(raw, "2.2")),
        "battery": _num(raw, "3.1"),
        "charging": None if charging is None else charging == CHARGING,
        # On the dock by its state, or by charging or charged whatever it is doing there
        # (drying, washing, updating or waiting on the dock: 3.2 = 3).
        "on_dock": _either(code in DOCKED, charging in (CHARGING, CHARGED), code, charging),
        "fully_charged": _either(code == 13, charging == CHARGED, code, charging),
        "error": _errors(raw, trusted),
        "area": _num(raw, "4.3"),
        "time": _num(raw, "4.2"),
        "done": _num(raw, "4.63"),
        "water_tank": _looked_up(tank, WATER_TANK),
        # Only a tank fitted with its mop (10) says the mop is on; 4.53 is never read.
        "mop_pad": "attached" if tank == 10 else None,
        "water_flow": _looked_up(_num(raw, "4.5"), WATER_FLOW),
        "dust_bin": None,  # no property reports how full the bin is
        "auto_empty": None if auto is None else bool(auto),
        "dust_bag": _looked_up(_num(raw, "27.3"), DUST_BAG),
        "consumables": _consumables(raw),
    }
    # Not known to be off the dock (on it, or not read) counts as on it: that only adds the note.
    # RULE: a tank whose dock is unknown comes from the cache
    lagging = LAGS_ON_THE_DOCK if values["on_dock"] is not False else frozenset()
    return Readings(**{f: Reading(values[f], f in trusted, as_of, _source(f, lagging, source)) for f in FIELDS})


#: Readings the cloud holds for minutes while the robot is docked: it reported the tank
#: fitted the whole time it was out on 18.11 (notes n1, n4).
LAGS_ON_THE_DOCK = frozenset({"water_tank", "mop_pad"})


def _source(field: str, lagging: frozenset[str], source: Source) -> Source:
    # RULE: the tank and mop on the dock come from the cache
    return "cache" if field in lagging else source


# -- the brand, as the voice server needs it (vacuum_mcp.backend.Backend) ---------------
# The cloud client, the login wait and the simulator are imported only when asked for,
# so loading the readings never loads the cloud library.

CLOUD = "the Dreame cloud"
APP = "the DreameHome app"
ACCOUNT = "DreameHome"
LOGIN = "Dreame login"


#: Words in the client's account of a failed login, and what kind of trouble each means.
#: The first match counts.
_TROUBLES: tuple[tuple[LoginTrouble, tuple[str, ...]], ...] = (
    ("refused", ("70016", "credentials")),
    ("extra_check", ("2fa", "verification", "captcha")),
    ("rate_limited", ("rate-limited",)),
)


def login_trouble(cloud_error: str | None) -> LoginTrouble | None:
    """Why the cloud refused a login, from the client's own words for it."""
    text = (cloud_error or "").lower()
    return next((trouble for trouble, words in _TROUBLES if any(word in text for word in words)), None)


def wait_reason(cloud_error: str | None) -> str:
    """The reason code kept in the shared login wait (never the cloud's words)."""
    from dreame_mcp.login_wait import reason_of

    return reason_of(cloud_error)


def login_wait(clock: Callable[[], float]) -> LoginWait:
    """The login wait shared with the web settings page."""
    from dreame_mcp.login_wait import LoginWait as SharedWait

    return SharedWait(clock=clock)


def client(secrets_dir: str | None) -> VacuumClient | None:
    """The DreameHome cloud client, or None when no login is set up."""
    from dreame_mcp.client import client_from_env

    return client_from_env(secrets_dir)


def simulator() -> VacuumClient:
    """A pretend vacuum: no login, no cloud."""
    from dreame_mcp.simulator import SimulatedVacuum

    return SimulatedVacuum()
