"""The vacuum layer: neutral readings, and a Dreame backend that alone knows Dreame's codes.

Design vacuum-mcp-v1.md (v1.1) step 1, first slice. Readings are what Roy's L10s Plus
(dreame.vacuum.r2363a) reported, made up only where a test says so.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from vacuum_mcp.backends import backend, dreame
from vacuum_mcp.capabilities import COMMANDS, NOTHING, Capabilities
from vacuum_mcp.readings import FIELDS, STATE_NAMES

NEUTRAL = Path(__file__).resolve().parent.parent / "src" / "vacuum_mcp"
L10S_PLUS = "dreame.vacuum.r2363a"
#: Docked and charged, tank and mop fitted, the bin-full warning on (as read 18.11 at 17.09).
DOCKED: dict[str, int] = {
    "2.1": 13, "2.2": 0, "3.1": 100, "3.2": 1, "4.35": 101, "4.6": 1, "4.53": 0, "15.1": 1,
    "9.1": 263, "9.2": 87, "16.1": 0, "16.2": 0, "18.1": 100, "18.2": 80,
}  # fmt: skip


def _read(model: str | None = L10S_PLUS, **change: int):
    return dreame.read({**DOCKED, **change}, model, as_of=1000.0, source="cache")


def _neutral_files():
    return [p for p in NEUTRAL.rglob("*.py") if "backends" not in p.relative_to(NEUTRAL).parts]


def test_no_dreame_code_outside_the_backends():
    assert _neutral_files()
    for path in _neutral_files():
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"""["']\d+\.\d+["']""", text), f"a siid.piid code in {path.name}"
        assert "backends.dreame" not in text and "dreame" not in text.casefold(), f"Dreame named in {path.name}"


def test_backends_are_found_by_name():
    assert backend("dreame") is dreame
    with pytest.raises(ValueError, match="no backend for 'roomba'"):
        backend("roomba")


def test_a_read_fills_every_field_with_its_time_and_source():
    readings = _read()
    for field in FIELDS:
        reading = getattr(readings, field)
        assert (reading.as_of, reading.source) == (1000.0, "cache")


def test_the_readings_are_words_and_numbers_not_codes():
    readings = _read()
    assert readings.state.value == "docked"
    assert readings.battery.value == 100 and readings.charging.value is True
    assert readings.error.value == ("the dust bin is full",)
    assert readings.water_tank.value == "fitted"
    assert readings.auto_empty.value is True
    assert readings.consumables.value == {
        "main_brush": {"percent": 87, "hours": 263}, "mop_pad": {"percent": 100, "hours": 80},
        "sensors": {"percent": 0, "hours": 0},
    }  # fmt: skip


def test_every_dreame_state_has_a_neutral_name():
    assert {name for name, _ in dreame.STATES.values()} <= STATE_NAMES


def test_on_the_l10s_plus_the_tank_is_trusted_and_the_mop_is_not():
    readings = _read()
    assert readings.water_tank.known
    assert not readings.mop_pad.known and not readings.mop_pad.trusted


@pytest.mark.parametrize("pad", [0, 1])
def test_on_the_l10s_plus_4_53_is_never_read(pad):
    assert _read(**{"4.53": pad}) == _read()


def test_on_the_l10s_plus_a_tank_with_its_mop_is_still_not_trusted_for_the_mop():
    mop = _read(**{"4.6": 10}).mop_pad
    assert mop.value == "attached" and not mop.trusted


def test_on_the_l10s_plus_the_bin_fill_is_not_reported():
    assert _read().dust_bin.value is None and not _read().dust_bin.trusted


def test_a_model_not_in_the_table_is_trusted_only_on_the_common_readings():
    readings = _read(model="dreame.vacuum.unknown")
    trusted = {f for f in FIELDS if getattr(readings, f).trusted}
    assert trusted == {"state", "activity", "battery", "charging", "on_dock", "fully_charged", "error", "area", "time"}
    assert dreame.capabilities(None).commands == COMMANDS


def test_a_reading_the_robot_left_out_has_no_value():
    raw = dict(DOCKED)
    del raw["4.6"], raw["3.1"]
    readings = dreame.read(raw, L10S_PLUS, as_of=0.0, source="fresh")
    assert readings.water_tank.value is None and not readings.water_tank.known
    assert readings.battery.value is None


def test_capabilities_name_only_known_commands_and_readings():
    assert NOTHING.commands == frozenset() and NOTHING.readings == frozenset()
    with pytest.raises(ValueError, match="unknown commands"):
        Capabilities(commands=frozenset({"fly"}), readings=frozenset())
    with pytest.raises(ValueError, match="unknown readings"):
        Capabilities(commands=frozenset(), readings=frozenset({"tank"}))


#: Problems drawn from other readings: no water (4.41 = 6), the base's bag missing (27.3 = 1)
#: and a failed empty (15.5 = 2). Made up.
DERIVED = {"4.35": 0, "4.41": 6, "27.3": 1, "15.5": 2}


def test_an_unknown_model_names_no_problem_drawn_from_a_reading_it_isnt_trusted_on():
    assert _read(model="dreame.vacuum.unknown", **DERIVED).error.value == ()


def test_the_l10s_plus_names_the_problems_drawn_from_its_trusted_readings():
    assert _read(**DERIVED).error.value == (
        "the water tank is out", "the dust bag in the base is not fitted", "the base didn't empty the bin",
    )  # fmt: skip


def test_problems_are_read_even_when_the_state_is_left_out():
    raw = {k: v for k, v in {**DOCKED, **DERIVED}.items() if k != "2.1"}
    readings = dreame.read(raw, L10S_PLUS, as_of=0.0, source="cache")
    assert "the base didn't empty the bin" in (readings.error.value or ())


def test_only_readings_the_model_is_trusted_on_count_as_a_problems_read():
    """An unknown model with only untrusted problem readings has no error read: None, not ()."""
    raw = {"4.41": 6, "27.3": 1, "15.5": 2}
    assert dreame.read(raw, "dreame.vacuum.unknown", as_of=0.0, source="cache").error.value is None


@pytest.mark.parametrize(
    ("change", "words"),
    [
        ({"2.1": 25}, "cleaning a second time"),
        ({"2.1": 2, "3.2": 1}, "charging on the dock"),
        ({"2.1": 4, "2.2": 7}, "stopped with a problem, error code 7; the DreameHome app says what it means"),
        ({"2.1": 77}, "busy with something I have no name for (state 77)"),
    ],
)
def test_the_activity_is_in_the_backends_own_words(change, words):
    assert _read(**change).activity.value == words
