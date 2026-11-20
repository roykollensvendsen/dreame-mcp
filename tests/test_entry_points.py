"""The voice server lives in the brand-neutral vacuum_mcp and starts as vacuum-voice-mcp,
while the old name dreame-voice-mcp keeps starting the very same server, so the bridge's
plugin entry never has to change. The old name is kept for good: switching the command in
the owner's config counts as moving the plugin and needs the owner's typed approve."""

import subprocess
import sys
import tomllib
from pathlib import Path

import dreame_mcp.voice
import vacuum_mcp.voice

ROOT = Path(__file__).resolve().parents[1]


def _scripts() -> dict[str, str]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["scripts"]


def test_the_voice_server_starts_under_its_new_name():
    assert _scripts()["vacuum-voice-mcp"] == "vacuum_mcp.voice:main"


def test_the_old_name_still_starts_the_same_server():
    assert _scripts()["dreame-voice-mcp"] in ("vacuum_mcp.voice:main", "dreame_mcp.voice:main")
    assert dreame_mcp.voice.main is vacuum_mcp.voice.main


def test_the_voice_server_lives_in_the_neutral_layer():
    assert vacuum_mcp.voice.Vacuum.__module__ == "vacuum_mcp.voice"
    assert vacuum_mcp.voice.build_server.__module__ == "vacuum_mcp.voice"


def test_both_module_names_start_the_server():
    for module in ("vacuum_mcp.voice", "dreame_mcp.voice"):
        shown = subprocess.run([sys.executable, "-m", module, "--help"], capture_output=True, text=True, timeout=120)
        assert shown.returncode == 0, shown.stderr
        assert "--simulate" in shown.stdout


async def test_the_server_is_named_after_its_command():
    from fastmcp import Client

    from vacuum_mcp.voice import Link, Vacuum, build_server

    async with Client(build_server(Vacuum(Link(None)))) as client:
        assert client.initialize_result.serverInfo.name == "vacuum-voice"
