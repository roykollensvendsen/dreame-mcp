"""One wait after a failed cloud login, shared by the voice server and the web page.

The Dreame cloud counts failed logins and then asks for a captcha (87001), so after a
failed login neither side should log in again for a while. The voice server and the web
server are two processes with a client each, so the wait is kept in a small file both
read: ``$XDG_STATE_HOME/dreame-mcp/last-failed-login`` (``~/.local/state`` by default).

It holds only when the login failed and a reason code, never anything from the login or
from the cloud's answer. A file that cannot be read, or one dated after now, is no wait,
so a broken file can never lock the vacuum out; and however far off its own time says
the wait ends, a kept wait ends at most ``LONGEST`` after the file was written.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from pathlib import Path

#: Seconds each reason code waits.
WAITS = {"failed": 60.0}
#: No kept wait lasts longer than this after its file was written.
LONGEST = max(WAITS.values())


def default_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "dreame-mcp" / "last-failed-login"


class LoginWait:
    """The shared wait: ``failed()`` after a refused cloud login, ``succeeded()`` after a
    good one, and ``left()`` for the seconds until the next login may be tried."""

    def __init__(self, path: Path | Callable[[], Path] = default_path, *, clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock  # wall time: it is compared with the file's mtime

    @property
    def path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    def failed(self, reason: str = "failed") -> None:
        """Start the wait. A reason that is not a known code is kept as "failed"."""
        path = self.path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        new = path.with_name(path.name + ".new")
        new.unlink(missing_ok=True)
        text = json.dumps({"at": self._clock(), "reason": reason if reason in WAITS else "failed"})
        # RULE: the wait file is private
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(new, path)  # whole or not at all: a reader never sees half a file

    def succeeded(self) -> None:
        """End the wait for both processes."""
        # RULE: a successful login ends the wait
        self.path.unlink(missing_ok=True)

    def left(self) -> float:
        """Seconds until a login may be tried again; 0 when it may be now."""
        path = self.path
        try:
            written = path.stat().st_mtime
            kept = json.loads(path.read_text(encoding="utf-8"))
            ends = float(kept["at"]) + WAITS[kept["reason"]]
        # RULE: a damaged wait file counts as no wait
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0
        now = self._clock()
        # RULE: a wait file from the future counts as no wait
        if written > now:
            return 0.0
        # RULE: a kept wait ends at most the longest wait after the file was written
        ends = min(ends, written + LONGEST)
        left = ends - now
        return left if math.isfinite(left) and left > 0 else 0.0
