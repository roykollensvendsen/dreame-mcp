"""The brand backends, found by name so the neutral layer imports none of them directly."""

from __future__ import annotations

import importlib
from types import ModuleType

#: Brand name -> module that implements it.
BACKENDS: dict[str, str] = {"dreame": "vacuum_mcp.backends.dreame"}


def backend(brand: str) -> ModuleType:
    """The module for a brand, loaded on first use."""
    try:
        return importlib.import_module(BACKENDS[brand])
    except KeyError:
        raise ValueError(f"no backend for {brand!r}; known: {', '.join(sorted(BACKENDS))}") from None
