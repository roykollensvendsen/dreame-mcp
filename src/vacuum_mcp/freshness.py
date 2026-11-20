"""When a reading may be behind, and the words that say so.

A backend fills each reading's ``as_of`` and ``source``. A reading older than
``FRESH_SECONDS`` is said with its age; one the backend marks as the cloud's cached
copy is said as last reported. A fresh reading is never said as behind, and a reading
the model can't be trusted on gets no age: it is not said at all.
"""

from __future__ import annotations

from collections.abc import Iterable

from .readings import Reading

#: A reading this many seconds old, or younger, is fresh.
FRESH_SECONDS = 60
#: What every note ends with.
BEHIND = "the cloud may be behind"


def age_words(seconds: float) -> str:
    """'45 seconds' under a minute, else whole minutes: '1 minute', '3 minutes'."""
    count, unit = (int(seconds), "second") if seconds < 60 else (int(seconds // 60), "minute")
    return f"{count} {unit}{'' if count == 1 else 's'}"


def when(reading: Reading[object], now: float) -> str | None:
    """'as of 3 minutes ago' or 'when last reported' for a reading that may be behind, else None."""
    if not reading.known:
        return None
    age = now - reading.as_of
    # RULE: a reading older than a minute is said with its age
    if age > FRESH_SECONDS:
        return f"as of {age_words(age)} ago"
    # RULE: a cached reading is said as last reported
    if reading.source == "cache":
        return "when last reported"
    return None


def note(spoken: Iterable[tuple[str, Reading[object]]], now: float) -> str:
    """One sentence for the spoken readings that may be behind, each said once:
    'The water tank showed fitted when last reported; the cloud may be behind.'
    Empty when every one is fresh."""
    groups: dict[str, list[str]] = {}
    for words, reading in spoken:
        timing = when(reading, now)
        if timing and words not in groups.setdefault(timing, []):
            groups[timing].append(words)
    if not groups:
        return ""
    text = "; ".join(f"{' and '.join(said)} {timing}" for timing, said in groups.items())
    return f"{text[:1].upper()}{text[1:]}; {BEHIND}."
