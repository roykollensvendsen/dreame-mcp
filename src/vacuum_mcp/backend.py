"""What the voice server needs from a brand's backend, and from that brand's client.

The voice server is written against these protocols only, so it names no brand: the
cloud's and the app's names in what it says, the login wait, and the client all come
from the backend. A backend module fits ``Backend`` as it is (its constants and
functions are the members); ``backends.default`` is typed to return one, so pyright
proves that the module fits.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, Protocol

from .capabilities import Capabilities
from .readings import Readings, Source

#: Why a login failed, as far as the voice server tells them apart.
LoginTrouble = Literal["refused", "extra_check", "rate_limited"]


class Status(Protocol):
    """A status read: what the client got, or why it got nothing."""

    @property
    def error(self) -> str | None: ...
    @property
    def raw(self) -> dict[str, Any]: ...


class VacuumClient(Protocol):
    """What the voice server needs from a client: the brand's cloud client, or a simulator."""

    @property
    def connected(self) -> bool: ...
    @property
    def cloud_error(self) -> str | None: ...
    @property
    def model(self) -> str | None:
        """The model the login found, or None when it found none."""
        ...

    async def connect(self) -> bool: ...
    async def get_status(self) -> Status: ...
    async def control(self, cmd: str) -> dict: ...
    async def get_map(self) -> dict: ...
    async def clean_rooms(self, room_ids: list[int]) -> dict: ...
    async def get_full_status(self) -> dict: ...
    def disconnect(self) -> None: ...


class LoginWait(Protocol):
    """The wait after a failed login, shared with any other process logging in."""

    def failed(self, reason: str = ...) -> None: ...
    def succeeded(self) -> None: ...
    def left(self) -> float: ...


class Backend(Protocol):
    """A brand: its names in speech, its readings, its login and its client."""

    # Read-only, so a module's constants fit as they are.
    @property
    def CLOUD(self) -> str:
        """The cloud as it is said, starting with "the"."""
        ...

    @property
    def APP(self) -> str:
        """The brand's app as it is said, starting with "the"."""
        ...

    @property
    def ACCOUNT(self) -> str:
        """The account the login is for, as it is said before "login"."""
        ...

    @property
    def LOGIN(self) -> str:
        """The login as it is said after "its" or "a": "its ... is missing", "A ... failed"."""
        ...

    @property
    def PARTS(self) -> tuple[tuple[str, str, str, str], ...]:
        """Wear parts: (name in the answer, words, and where the backend reads them)."""
        ...

    def read(self, raw: dict[str, Any], model: str | None, as_of: float, source: Source) -> Readings: ...
    def known_model(self, model: str | None) -> bool: ...
    def capabilities(self, model: str | None) -> Capabilities: ...
    def login_trouble(self, cloud_error: str | None) -> LoginTrouble | None: ...
    def wait_reason(self, cloud_error: str | None) -> str: ...
    def login_wait(self, clock: Callable[[], float]) -> LoginWait: ...
    def client(self, secrets_dir: str | None) -> VacuumClient | None: ...
    def simulator(self) -> VacuumClient: ...
