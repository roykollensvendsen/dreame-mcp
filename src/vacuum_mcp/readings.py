"""What a vacuum reports, in fields every brand fills the same way.

Each field is a ``Reading``: its value, whether the backend trusts it on this
model, when it was read, and where it came from. A reading the model can't give,
or can't be trusted on, has ``trusted=False``; the spoken answer then says the
vacuum can't report it, and never guesses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

#: Where a reading came from: pushed by the robot, read fresh just now, or the cloud's
#: cached copy, which can lag (a docked, charged robot may not report a change for minutes).
Source = Literal["push", "fresh", "cache"]


@dataclass(frozen=True)
class Reading[T]:
    """One reading: ``value`` is None when the robot left it out."""

    value: T | None
    trusted: bool
    as_of: float
    source: Source

    @property
    def known(self) -> bool:
        """True when there is a value and the backend trusts it on this model."""
        return self.trusted and self.value is not None


@dataclass(frozen=True)
class Readings:
    """Everything one read gave, in fixed fields.

    Values are plain words or numbers, never a brand's codes:
    ``state`` one of the names in ``STATE_NAMES``; ``error`` the problems in words;
    ``water_tank`` "fitted", "out" or "in the station"; ``mop_pad`` "attached" or
    "not attached"; ``water_flow`` "low", "medium" or "high" (a setting); ``dust_bag``
    the base's bag, "fitted", "not fitted" or "to be checked"; ``done`` the percent of this
    clean done; ``consumables`` each part's percent and hours left. ``error`` holds every
    problem the backend can name, in words.
    """

    state: Reading[str]
    battery: Reading[int]
    charging: Reading[bool]
    error: Reading[tuple[str, ...]]
    area: Reading[int]
    time: Reading[int]
    done: Reading[int]
    water_tank: Reading[str]
    mop_pad: Reading[str]
    water_flow: Reading[str]
    dust_bin: Reading[str]
    auto_empty: Reading[bool]
    dust_bag: Reading[str]
    consumables: Reading[dict[str, dict[str, int]]]


#: The field names of ``Readings``, as capabilities name them.
FIELDS: tuple[str, ...] = tuple(Readings.__dataclass_fields__)
#: The state names a backend may give.
STATE_NAMES = frozenset(
    {"cleaning", "mopping", "idle", "paused", "error", "returning", "charging", "docked", "drying", "washing",
     "mapping", "updating", "emptying"}
)  # fmt: skip
