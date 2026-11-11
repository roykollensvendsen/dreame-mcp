"""The workbench tool dreame(operation=...): offline reasons, errors and every line of the map summary.

Offline: the client is a fake, the shared state is set per test and put back.
"""

from __future__ import annotations

import pytest

from dreame_mcp.client import DreameStatus
from dreame_mcp.portmanteau import (
    _format_map_md,
    _map_result,
    dreame_tool,
    execute_control_data,
    fetch_map_data,
    fetch_status_data,
    offline_reasons,
)
from dreame_mcp.state import _state


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    saved = dict(_state)
    _state.clear()
    yield _state
    _state.clear()
    _state.update(saved)


class Broken:
    async def get_status(self):
        return DreameStatus(error="cloud said no")

    async def get_map(self):
        raise RuntimeError("socket closed")


# -- why control is down -------------------------------------------------------


def test_not_configured_says_what_to_set():
    (reason,) = offline_reasons()
    assert reason.startswith("not configured: set DREAME_USER/DREAME_PASSWORD")


def test_configured_but_down_names_both_paths(fresh_state):
    fresh_state.update(configured=True, connection={"ip": "10.0.0.9", "cloud_error": "login refused"})
    local, cloud = offline_reasons()
    assert local.startswith("local path down: no miio answer at 10.0.0.9:54321")
    assert cloud == "cloud path down: login refused"


def test_configured_without_a_path_says_so(fresh_state):
    fresh_state.update(configured=True, connection=None)
    assert offline_reasons() == ["no control path configured (set DREAME_IP or DREAME_USER/DREAME_PASSWORD)"]


async def test_status_without_a_client_when_not_configured():
    data = await fetch_status_data(None)
    assert data["success"] is False
    assert data["error"].startswith("Not configured. not configured:")
    assert (data["unconfigured"], data["offline"]) == (True, False)


async def test_status_without_a_client_when_offline(fresh_state):
    fresh_state.update(configured=True, connection={"ip": "10.0.0.9", "did": "123"})
    data = await fetch_status_data(None)
    assert data["error"].startswith("Robot offline. local path down")
    assert (data["unconfigured"], data["offline"], data["ip"], data["did"]) == (False, True, "10.0.0.9", "123")


async def test_a_status_error_is_passed_on():
    assert await fetch_status_data(Broken()) == {"success": False, "error": "cloud said no"}


async def test_map_and_control_without_a_client():
    assert (await fetch_map_data(None))["error"].startswith("Disconnected")
    assert (await execute_control_data(None, "stop"))["error"].startswith("No client")


# -- the tool ------------------------------------------------------------------


async def test_battery_unknown_when_the_status_fails(fresh_state):
    fresh_state["client"] = Broken()
    assert await dreame_tool(operation="battery") == "### Battery: Unknown"


async def test_a_crash_is_said_with_the_correlation_id(fresh_state):
    class Ctx:
        correlation_id = "abc123"

    fresh_state["client"] = Broken()
    text = await dreame_tool(ctx=Ctx(), operation="map")  # type: ignore[arg-type]
    assert text.startswith("### Error: crash in `dreame(map)`")
    assert "socket closed" in text and "`abc123`" in text


async def test_a_failed_command_says_the_error():
    text = await dreame_tool(operation="stop")
    assert text.startswith("### Control failed")
    assert "No client - check environment variables." in text


# -- the map summary -----------------------------------------------------------


def _lines(data: dict) -> list[str]:
    return _format_map_md({"success": True, **data}).splitlines()


def test_a_failed_map_says_the_error():
    assert "**Error:** no map yet" in _format_map_md({"success": False, "error": "no map yet"})


def test_a_live_map_summary_names_rooms_robot_trail_and_zones():
    lines = _lines(
        {
            "source": "live",
            "object_name": "ali/1",
            "raw_bytes": 2048,
            "image": "x",
            "render_error": "font missing",
            "map_data": {
                "room_list": [{"id": 1, "name": "Kitchen"}],
                "robot_room": {"id": 1, "name": "Kitchen"},
                "docked": True,
                "robot_position": {"x": 3, "y": 4},
                "path": [1, 2, 3],
                "virtual_walls": [1],
                "no_go_areas": [1, 2],
                "no_mop_areas": [],
            },
        }
    )
    assert lines[:4] == [
        "## LIDAR Map Summary",
        "- **Map:** live",
        "- **Object Name:** `ali/1`",
        "- **Raw Size:** 2.0 KB",
    ]
    assert "- **Rooms:** Kitchen (id 1)" in lines
    assert "- **Robot:** on its dock, in Kitchen (id 1)" in lines
    assert "- **Robot Position:** (3, 4)" in lines
    assert "- **Movement Trail:** 3 points" in lines
    assert "- **Restricted Zones:** 1 Walls, 2 No-Go, 0 No-Mop" in lines
    assert "> The rendered map is attached as an image." in lines
    assert "> Render error: font missing" in lines


def test_outside_every_room_is_said():
    lines = _lines({"map_data": {"room_list": [{"id": 1, "name": "Hall"}], "robot_room": None}})
    assert "- **Robot:** not inside a known room" in lines


def test_a_saved_map_does_not_say_where_the_robot_is():
    lines = _lines(
        {
            "source": "saved",
            "map_data": {"rooms": 4, "robot_room": {"id": 1, "name": "Hall"}, "robot_position": {"x": 1}},
        }
    )
    assert lines[1].startswith("- **Map:** saved")
    assert "- **Rooms Detected:** 4" in lines
    assert not any("Robot" in line for line in lines[2:])


def test_a_live_map_without_rooms_says_nothing_about_the_robot():
    lines = _lines({"map_data": {}})
    assert lines[-1] == "- **Rooms Detected:** 0"


def test_a_picture_that_is_not_base64_leaves_only_the_text():
    data = {"success": True, "image": "!!not base64!!", "map_data": None}
    assert _map_result(data) == _format_map_md(data)
