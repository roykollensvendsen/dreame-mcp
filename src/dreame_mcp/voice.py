"""The old name of the voice server, kept so `dreame-voice-mcp` and `python -m dreame_mcp.voice`
start exactly the same server as `vacuum-voice-mcp`. The server itself lives in the
brand-neutral `vacuum_mcp.voice`. This alias goes once the owner has switched the bridge's
plugin entry to the new name."""

from vacuum_mcp.voice import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
