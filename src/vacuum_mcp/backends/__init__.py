"""The brand backends, found by name so the neutral layer imports none of them directly."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..backend import Backend

#: Brand name -> module that implements it.
BACKENDS: dict[str, str] = {"dreame": "vacuum_mcp.backends.dreame"}


def backend(brand: str) -> ModuleType:
    """The module for a brand, loaded on first use."""
    try:
        return importlib.import_module(BACKENDS[brand])
    except KeyError:
        raise ValueError(f"no backend for {brand!r}; known: {', '.join(sorted(BACKENDS))}") from None


def default() -> Backend:
    """The backend the voice server runs with. Typed, so pyright proves the module fits
    ``Backend``: a missing name fails the type check, not a voice call."""
    from . import dreame

    return dreame
