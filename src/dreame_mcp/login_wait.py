"""One wait after a failed cloud login, shared by the voice server and the web page.

The Dreame cloud counts failed logins and then asks for a captcha (87001), so after a
failed login neither side should log in again for a while. The voice server and the web
server are two processes with a client each, so the wait is kept in a small file both
read: ``$XDG_STATE_HOME/dreame-mcp/last-failed-login`` (``~/.local/state`` by default).

It holds only when the login failed and a reason code, never anything from the login or
from the cloud's answer. A file that cannot be read, or one dated after now, is no wait,
so a broken file can never lock the vacuum out; and however far off its own time says
the wait ends, a kept wait ends at most its own reason's wait after the file was written.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("dreame-mcp.login_wait")

#: Seconds each reason code waits.
WAITS = {
    "failed": 60.0,
    # After 87001 the cloud wants "a few minutes"; logging in sooner keeps it refusing.
    # RULE: a rate-limited login waits a few minutes
    "rate-limited": 300.0,
}


def reason_of(cloud_error: str | None) -> str:
    """The reason code for a refused login, from the client's own words for it
    (client._classify_cloud_failure): only the code is kept, never the words."""
    return "rate-limited" if "rate-limited (87001)" in (cloud_error or "") else "failed"


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
        self._keep(lambda: self._write(reason if reason in WAITS else "failed"))

    def succeeded(self) -> None:
        """End the wait for both processes."""
        # RULE: a successful login ends the wait
        self._keep(lambda: self.path.unlink(missing_ok=True))

    def _keep(self, change: Callable[[], object]) -> None:
        """Change the file, best effort: it only records a login, so a full disk, a quota or
        a folder that can't be written must never stop or lose the login itself."""
        try:
            change()
        # RULE: writing the wait file never stops a login
        except OSError as e:
            logger.warning(
                "Could not update the login-wait file %s (%s); the voice and the web page "
                "won't wait for each other until that folder can be written to",
                self.path,
                e,
            )

    def _write(self, reason: str) -> None:
        path = self.path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # One name per writer, so the voice and the web page failing at the same moment
        # can't remove or trip over each other's half-written file.
        new = path.with_name(f"{path.name}.{os.getpid()}.new")
        new.unlink(missing_ok=True)
        at = self._clock()
        text = json.dumps({"at": at, "reason": reason})
        # RULE: the wait file is private
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        # Stamp it with the time it records, but never ahead of the real clock: a file
        # system may round its own stamp a little behind, and the cap on a wait is counted
        # from the stamp; a writer whose clock runs ahead (the voice's, after the wall
        # clock was stepped back) must not leave a file the other side reads as future.
        stamp = min(at, time.time())
        os.utime(new, (stamp, stamp))
        os.replace(new, path)  # whole or not at all: a reader never sees half a file

    def left(self) -> float:
        """Seconds until a login may be tried again; 0 when it may be now."""
        path = self.path
        try:
            written = path.stat().st_mtime
            kept = json.loads(path.read_text(encoding="utf-8"))
            wait = WAITS[kept["reason"]]
            ends = float(kept["at"]) + wait
        # RULE: a damaged wait file counts as no wait
        except (OSError, ValueError, KeyError, TypeError):
            return 0.0
        now = self._clock()
        # RULE: a wait file from the future counts as no wait
        if written > now:
            return 0.0
        # RULE: a kept wait ends at most its own wait after the file was written
        ends = min(ends, written + wait)
        left = ends - now
        return left if math.isfinite(left) and left > 0 else 0.0
