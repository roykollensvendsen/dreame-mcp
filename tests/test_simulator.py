"""The pretend vacuum: what it answers before login, and the commands it refuses."""

from __future__ import annotations

from dreame_mcp.simulator import RETURNING, TRIP_HOME, SimulatedVacuum


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_before_login_every_call_says_not_connected():
    sim = SimulatedVacuum()
    assert (await sim.get_status()).error == "Not connected"
    assert (await sim.control("stop"))["success"] is False
    assert (await sim.clean_rooms([1]))["success"] is False
    assert (await sim.get_map())["success"] is False
    assert sim.sent == []


async def test_an_unknown_command_or_room_is_refused():
    sim = SimulatedVacuum()
    await sim.connect()
    assert await sim.control("dance") == {"success": False, "error": "Unknown command: dance"}
    assert (await sim.clean_rooms([1, 99]))["error"] == "Unknown rooms: [1, 99]"
    assert sim.sent == []


async def test_offline_it_never_logs_in():
    sim = SimulatedVacuum(offline=True)
    assert await sim.connect() is False
    assert sim.cloud_error == "simulated: cloud unreachable"


async def test_heading_home_it_is_in_the_hallway_and_then_charges():
    clock = Clock()
    sim = SimulatedVacuum(clock=clock, docked=False, battery=50)
    await sim.connect()
    await sim.control("go_home")
    assert (await sim.get_map())["map_data"]["robot_room"]["name"] == "Hallway"
    assert (await sim.get_status()).raw["2.1"] == RETURNING
    clock.now += TRIP_HOME
    status = await sim.get_status()
    assert status.is_charging and status.raw["3.2"] == 1
    sim.disconnect()
    assert not sim.connected
