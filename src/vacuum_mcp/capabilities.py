"""What a vacuum model can do, and which of its readings can be trusted.

Every tool is always offered; a model's capabilities decide whether it acts or
answers in words that it can't. A backend gives one ``Capabilities`` per model.
"""

from __future__ import annotations

from dataclasses import dataclass

from .readings import FIELDS

#: The commands a backend may offer.
COMMANDS = frozenset({"start", "start_rooms", "pause", "stop", "dock", "locate"})
#: Each command as it is said after "This vacuum can't ...".
COMMAND_WORDS = {
    "start": "start a full clean",
    "start_rooms": "clean chosen rooms",
    "pause": "pause",
    "stop": "stop where it is",
    "dock": "go back to its dock by itself",
    "locate": "beep so you can find it",
}


@dataclass(frozen=True)
class Capabilities:
    """The commands a model can carry out and the readings it is trusted on."""

    commands: frozenset[str]
    readings: frozenset[str]

    def __post_init__(self) -> None:
        if unknown := self.commands - COMMANDS:
            raise ValueError(f"unknown commands: {sorted(unknown)}")
        if unknown := self.readings - set(FIELDS):
            raise ValueError(f"unknown readings: {sorted(unknown)}")


#: A model nothing is known about: it does nothing and reports nothing it could get wrong.
NOTHING = Capabilities(commands=frozenset(), readings=frozenset())
