"""The Dreame backend: Dreame's property codes in, neutral ``Readings`` out.

This is the only place in vacuum_mcp that knows Dreame's codes (siid.piid, state and
error numbers) and which Dreame model reports what. The numbers are the MIoT spec's for
a Dreame vacuum, the meanings the reference project's names for them (client.FULL_STATUS
says where each is read). Checked against Roy's L10s Plus (dreame.vacuum.r2363a).
"""

from __future__ import annotations

from typing import Any

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
_COMMON = frozenset({"state", "battery", "charging", "error", "area", "time"})
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


def _problem(code: int) -> str:
    return PROBLEMS.get(code, f"error code {code}, which the DreameHome app explains")


def _consumables(raw: dict[str, Any]) -> dict[str, dict[str, int]] | None:
    wear: dict[str, dict[str, int]] = {}
    for name, _, percent_at, hours_at in PARTS:
        left = {"percent": _num(raw, percent_at), "hours": _num(raw, hours_at)}
        if left["percent"] is not None:
            wear[name] = {k: v for k, v in left.items() if v is not None}
    return wear or None


def read(raw: dict[str, Any], model: str | None, as_of: float, source: Source) -> Readings:
    """One read's properties ({"siid.piid": value}) as neutral readings for this model."""
    trusted = capabilities(model).readings
    code, charging, tank, auto = _num(raw, "2.1"), _num(raw, "3.2"), _num(raw, "4.6"), _num(raw, "15.1")
    bag = None if (b := _num(raw, "27.3")) is None else DUST_BAG.get(b)
    errors = [_problem(c) for key in ("2.2", "4.35") if (c := _num(raw, key))]
    # A problem drawn from another reading is named only when the model trusts that reading.
    if "water_tank" in trusted and (low := LOW_WATER.get(_num(raw, "4.41") or 0)):
        errors.append(low)
    if "dust_bag" in trusted and bag in ("not fitted", "to be checked"):
        errors.append(f"the dust bag in the base is {bag}")
    if "auto_empty" in trusted and _num(raw, "15.5") == 2:
        errors.append("the base didn't empty the bin")
    reported = any(key in raw for key in ("2.2", "4.35", "4.41", "27.3", "15.5"))
    values: dict[str, Any] = {
        "state": _state(code, charging),
        "battery": _num(raw, "3.1"),
        "charging": None if charging is None else charging == CHARGING,
        "error": tuple(dict.fromkeys(errors)) if reported else None,
        "area": _num(raw, "4.3"),
        "time": _num(raw, "4.2"),
        "done": _num(raw, "4.63"),
        "water_tank": None if tank is None else WATER_TANK.get(tank),
        # Only a tank fitted with its mop (10) says the mop is on; 4.53 is never read.
        "mop_pad": "attached" if tank == 10 else None,
        "water_flow": None if (flow := _num(raw, "4.5")) is None else WATER_FLOW.get(flow),
        "dust_bin": None,  # no property reports how full the bin is
        "auto_empty": None if auto is None else bool(auto),
        "dust_bag": bag,
        "consumables": _consumables(raw),
    }
    return Readings(**{f: Reading(values[f], f in trusted, as_of, source) for f in FIELDS})
