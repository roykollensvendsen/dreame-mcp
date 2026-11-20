"""The old name of the voice server, kept so `dreame-voice-mcp` and `python -m dreame_mcp.voice`
start exactly the same server as `vacuum-voice-mcp`. The server itself lives in the
brand-neutral `vacuum_mcp.voice`. This alias is kept for good: changing the command in the
owner's bridge config counts as moving the plugin and needs the owner's typed approve, so
the old name must keep working whether or not that switch is ever made."""

from vacuum_mcp.voice import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
