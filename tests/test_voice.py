"""The voice server: spoken answers, the pretend vacuum, the cloud being down, and no secrets out.

Offline: the simulator or a fake client, never the real robot or cloud.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys

from fastmcp import Client

from dreame_mcp.client import DreameStatus
from dreame_mcp.simulator import SimulatedVacuum
from dreame_mcp.voice import Link, Reading, Vacuum, build_server, instructions

SIX = {"vacuum_status", "vacuum_clean_all", "vacuum_pause", "vacuum_stop", "vacuum_go_home", "vacuum_find"}
TOOLS = SIX | {"vacuum_where", "vacuum_rooms", "vacuum_clean_rooms", "vacuum_full_status"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _vacuum(client, *, simulated=False, ask_first=False, budget=14.0, clock=None) -> Vacuum:
    clock = clock or Clock()
    return Vacuum(
        Link(client, clock=clock),
        ask_first=ask_first,
        simulated=simulated,
        settle=0,
        budget=budget,
        clock=clock,
    )


async def _call(vacuum: Vacuum, tool: str, **args) -> dict:
    async with Client(build_server(vacuum)) as c:
        result = await c.call_tool(tool, args)
        return result.structured_content


# -- reading the state ----------------------------------------------------


def _reading(**raw) -> Reading:
    return Reading.of(DreameStatus(raw={k.replace("_", "."): v for k, v in raw.items()}))


def test_idle_on_a_charging_dock_counts_as_charging():
    r = _reading(**{"2_1": 2, "3_1": 84, "3_2": 1})
    assert r.state == "charging"
    assert r.docked
    assert r.words() == "charging on the dock, battery 84 percent"


def test_error_state_names_the_code():
    assert "error code 7" in _reading(**{"2_1": 4, "2_2": 7, "3_1": 50}).words()


def test_unknown_state_is_said_plainly():
    r = _reading(**{"2_1": 77, "3_1": 50})
    assert r.state == "state_77"
    assert "state 77" in r.words()


def test_not_charging_off_the_dock_is_not_docked():
    assert not _reading(**{"2_1": 2, "3_1": 50, "3_2": 2}).docked


# -- the tools on the pretend vacuum -----------------------------------------


async def test_the_tools_without_ask_first():
    async with Client(build_server(_vacuum(SimulatedVacuum()))) as c:
        assert {t.name for t in await c.list_tools()} == TOOLS


async def test_ask_first_adds_confirm():
    async with Client(build_server(_vacuum(SimulatedVacuum(), ask_first=True))) as c:
        assert {t.name for t in await c.list_tools()} == TOOLS | {"vacuum_confirm"}


async def test_status_on_the_dock():
    a = await _call(_vacuum(SimulatedVacuum()), "vacuum_status")
    assert a == {
        "success": True,
        "say": "The vacuum is on the dock, fully charged, battery 100 percent.",
        "state": "docked",
        "battery": 100,
    }


async def test_a_clean_from_start_to_dock():
    clock = Clock()
    sim = SimulatedVacuum(clock=clock)
    v = _vacuum(sim, clock=clock)
    async with Client(build_server(v)) as c:

        async def say(tool):
            return (await c.call_tool(tool, {})).structured_content

        a = await say("vacuum_clean_all")
        assert a["say"] == "Started. The vacuum is cleaning, battery 100 percent."
        assert (await say("vacuum_clean_all"))["say"].startswith("It's already cleaning")
        assert (await say("vacuum_pause"))["say"].startswith("Paused. The vacuum is paused")
        assert (await say("vacuum_go_home"))["say"].startswith("Sent home. The vacuum is on its way back")
        assert (await say("vacuum_find"))["say"] == "The vacuum is beeping now, so you can find it."
    assert sim.sent == ["start_clean", "pause", "go_home", "find_robot"]


async def test_an_action_says_the_state_read_back_after_it():
    sim = SimulatedVacuum()
    reads = []
    real = sim.get_status

    async def counted():
        reads.append(len(sim.sent))
        return await real()

    sim.get_status = counted
    a = await _vacuum(sim).start()
    assert a["say"].startswith("Started. The vacuum is cleaning")
    # One read before the start was sent, one after it.
    assert reads == [0, 1]


async def test_nothing_to_stop_on_the_dock():
    sim = SimulatedVacuum()
    a = await _call(_vacuum(sim), "vacuum_stop")
    assert a["success"]
    assert a["say"].startswith("There's nothing to stop")
    assert sim.sent == []


async def test_already_home():
    a = await _call(_vacuum(SimulatedVacuum()), "vacuum_go_home")
    assert a["say"].startswith("It's already home")


async def test_a_simulated_answer_says_so():
    a = await _call(_vacuum(SimulatedVacuum(), simulated=True), "vacuum_status")
    assert a["say"].startswith("Simulated: ")
    assert instructions(False, True).startswith("SIMULATED")


def test_instructions_name_confirm_only_when_asking_first():
    assert "vacuum_confirm" not in instructions(False, False)
    assert "vacuum_confirm" in instructions(True, False)


async def test_simulator_heads_home_on_low_battery():
    clock = Clock()
    sim = SimulatedVacuum(battery=20, clock=clock)
    await sim.connect()
    await sim.control("start_clean")
    clock.now += 12 * 6
    assert (await sim.get_status()).raw["2.1"] == 5
    clock.now += 20
    await sim.get_status()
    assert (await sim.get_status()).raw["2.1"] == 6


# -- asking first --------------------------------------------------------------


async def test_a_confirm_needs_the_open_questions_token():
    sim = SimulatedVacuum()
    v = _vacuum(sim, ask_first=True)
    async with Client(build_server(v)) as c:
        q = (await c.call_tool("vacuum_clean_all", {})).structured_content
        assert q["question"] is True
        assert q["say"].endswith("Start a full clean now?")
        assert sim.sent == []
        wrong = (await c.call_tool("vacuum_confirm", {"token": "nope"})).structured_content
        assert not wrong["success"]
        # A wrong token closes the question; the right one no longer works either.
        late = (await c.call_tool("vacuum_confirm", {"token": q["token"]})).structured_content
        assert not late["success"]
        q = (await c.call_tool("vacuum_clean_all", {})).structured_content
        ok = (await c.call_tool("vacuum_confirm", {"token": q["token"]})).structured_content
        assert ok["say"].startswith("Started.")
        again = (await c.call_tool("vacuum_confirm", {"token": q["token"]})).structured_content
        assert not again["success"]
    assert sim.sent == ["start_clean"]


async def test_a_token_is_spent_only_once_the_start_was_sent():
    sim = SimulatedVacuum()
    v = _vacuum(sim, ask_first=True)
    q = await v.start()
    real, refused = sim.control, []

    async def refuse_once(cmd: str) -> dict:
        if not refused:
            refused.append(cmd)
            return {"success": False, "error": "simulated: not taken"}
        return await real(cmd)

    sim.control = refuse_once
    first = await v.confirm(q["token"])
    assert not first["success"] and first["say"].endswith("Ask me for its status before you try again.")
    second = await v.confirm(q["token"])
    assert second["say"].startswith("Started.")
    assert sim.sent == ["start_clean"]
    assert not (await v.confirm(q["token"]))["success"]


async def test_a_retried_confirm_does_not_start_twice():
    # A start that timed out may still have reached the robot: the retry reads first.
    sim = SimulatedVacuum()
    v = _vacuum(sim, ask_first=True)
    q = await v.start()
    real = sim.control

    async def lost_answer(cmd: str) -> dict:
        await real(cmd)
        return {"success": False, "error": "simulated: the answer was lost"}

    sim.control = lost_answer
    assert not (await v.confirm(q["token"]))["success"]
    sim.control = real
    again = await v.confirm(q["token"])
    assert again["success"] and again["say"].startswith("It's already")
    assert sim.sent == ["start_clean"]


async def test_a_question_expires():
    clock = Clock()
    v = _vacuum(SimulatedVacuum(clock=clock), ask_first=True, clock=clock)
    q = await v.start()
    clock.now += 121
    assert not (await v.confirm(q["token"]))["success"]


# -- the cloud being down ---------------------------------------------------


async def test_offline_status_says_cannot_reach():
    a = await _call(_vacuum(SimulatedVacuum(offline=True)), "vacuum_status")
    assert a == {"success": False, "say": "I can't reach the Dreame cloud right now. Try again in a minute."}


async def test_a_command_that_was_not_sent_says_so():
    a = await _call(_vacuum(SimulatedVacuum(offline=True)), "vacuum_clean_all")
    assert not a["success"]
    assert "The vacuum wasn't told anything." in a["say"]


async def test_not_set_up():
    a = await _call(_vacuum(None), "vacuum_status")
    assert not a["success"]
    assert "login is missing" in a["say"]


class FakeClient:
    """Login and reads that can fail or hang on cue."""

    def __init__(self, *, login_ok=True, cloud_error=None, hang_connect=0.0, hang_status=0.0):
        self.login_ok = login_ok
        self.cloud_error = cloud_error
        self.hang_connect = hang_connect
        self.hang_status = hang_status
        self.connected = False
        self.logins = 0
        self.map_answer: dict = {"success": False, "error": "no map"}

    async def connect(self) -> bool:
        self.logins += 1
        await asyncio.sleep(self.hang_connect)
        self.connected = self.login_ok
        return self.login_ok

    async def get_status(self) -> DreameStatus:
        await asyncio.sleep(self.hang_status)
        return DreameStatus(raw={"2.1": 13, "3.1": 90, "3.2": 3})

    async def control(self, cmd: str) -> dict:
        return {"success": True}

    async def get_map(self) -> dict:
        return self.map_answer

    async def clean_rooms(self, room_ids: list[int]) -> dict:
        return {"success": False, "error": "refused"}

    def disconnect(self) -> None:
        self.connected = False


async def test_a_failed_login_is_said_without_the_account():
    fake = FakeClient(login_ok=False, cloud_error="cloud login failed: Xiaomi rejected the credentials (70016 ...)")
    a = await _vacuum(fake).status()
    assert a["say"].startswith("The Dreame cloud turned down the login")


async def test_a_failed_login_waits_before_trying_again():
    clock = Clock()
    fake = FakeClient(login_ok=False)
    v = _vacuum(fake, clock=clock)
    await v.status()
    await v.status()
    assert fake.logins == 1
    clock.now += 61
    await v.status()
    assert fake.logins == 2


async def test_slow_login_says_still_logging_in():
    a = await _vacuum(FakeClient(hang_connect=5), budget=0.1, clock=_real_clock()).status()
    assert "still logging in" in a["say"]


async def test_a_slow_cloud_still_answers_within_the_budget():
    loop = asyncio.get_running_loop()
    began = loop.time()
    a = await _vacuum(FakeClient(hang_status=5), budget=0.2, clock=_real_clock()).status()
    assert not a["success"]
    assert "taking too long" in a["say"]
    assert loop.time() - began < 1


def _real_clock():
    import time

    return time.monotonic


# -- no secrets out, nothing but MCP on stdout -----------------------------------


async def test_the_password_is_never_in_an_answer_or_a_log(tmp_path, monkeypatch, caplog):
    from dreame_mcp import client as client_mod

    secrets_dir = tmp_path / "dreame"
    secrets_dir.mkdir()
    (secrets_dir / "user").write_text("owner@example.com\n")
    (secrets_dir / "password").write_text("hunter2-very-secret\n")
    for f in secrets_dir.iterdir():
        f.chmod(0o600)

    async def refused(self):
        self._cloud_error = client_mod._classify_cloud_failure(None)
        return False

    monkeypatch.setattr(client_mod.DreameHomeClient, "connect", refused)
    client = client_mod.client_from_env(secrets_dir)
    v = _vacuum(client)
    with caplog.at_level(logging.DEBUG):
        answers = [
            await _call(v, t, **({"rooms": ["Room 1"]} if t == "vacuum_clean_rooms" else {})) for t in sorted(TOOLS)
        ]
    text = json.dumps(answers) + caplog.text
    assert "hunter2-very-secret" not in text
    assert "owner@example.com" not in text


def test_stdout_carries_only_mcp():
    lines = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vacuum_status", "arguments": {}}},
    ]
    proc = subprocess.Popen(
        [sys.executable, "-m", "dreame_mcp.voice", "--simulate"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin and proc.stdout
    out = []
    for msg in lines:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        if "id" in msg:
            out.append(json.loads(proc.stdout.readline()))
    proc.stdin.close()
    rest = proc.stdout.read()
    proc.wait(timeout=10)
    assert rest.strip() == ""
    assert out[1]["result"]["structuredContent"]["say"].startswith("Simulated: The vacuum is on the dock")


def test_discover_warning_is_dropped_and_others_kept():
    from dreame_mcp.voice import _QuietDiscover

    def record(msg: str) -> logging.LogRecord:
        return logging.LogRecord("root", logging.WARNING, __file__, 1, msg, None, None)

    quiet = _QuietDiscover()
    assert not quiet.filter(
        record("Failed to validate request: 31 validation errors ... input_value='server/discover'")
    )
    assert quiet.filter(record("Failed to validate request: tools/call missing name"))
    assert quiet.filter(record("status read failed"))


# -- where it is, and its rooms ------------------------------------------------


async def test_where_on_the_dock():
    a = await _call(_vacuum(SimulatedVacuum()), "vacuum_where")
    assert a["say"] == "It's on its dock in Living room, fully charged, battery 100 percent."
    assert a["room"] == "Living room"


async def test_where_while_cleaning_moves_with_the_clock():
    clock = Clock()
    sim = SimulatedVacuum(clock=clock)
    v = _vacuum(sim, clock=clock)
    await v.start()
    assert (await v.where())["say"].startswith("It's in Living room, cleaning")
    clock.now += 31
    assert (await v.where())["say"].startswith("It's in Kitchen, cleaning")


async def test_rooms_are_named():
    a = await _call(_vacuum(SimulatedVacuum()), "vacuum_rooms")
    assert a["say"] == "The map has 3 rooms: Living room, Kitchen and Hallway."
    assert a["rooms"] == ["Living room", "Kitchen", "Hallway"]


def _map(**md):
    return {"success": True, "source": "live", "map_data": md}


async def test_where_on_a_map_without_rooms():
    fake = FakeClient()
    fake.map_answer = _map(room_list=[], robot_room=None, docked=False)
    a = await _vacuum(fake).where()
    assert a["say"].startswith("It's on a map without any rooms")


async def test_a_saved_map_does_not_claim_where_the_robot_is():
    fake = FakeClient()
    fake.map_answer = {**_map(room_list=[{"id": 1, "name": "Hall"}], robot_room=None), "source": "saved"}
    a = await _vacuum(fake).where()
    assert a["say"].startswith("I only have its saved map")


async def test_where_without_a_map_still_gives_the_state():
    a = await _vacuum(FakeClient()).where()
    assert not a["success"]
    assert (
        a["say"]
        == "I can't get the vacuum's map right now. The vacuum is on the dock, fully charged, battery 90 percent. "
        "Try again in a minute."
    )


async def test_rooms_without_a_map():
    a = await _vacuum(FakeClient()).rooms()
    assert not a["success"]
    assert "map" in a["say"]


# -- cleaning chosen rooms ---------------------------------------------------


async def test_clean_rooms_by_name_any_case():
    clock = Clock()
    sim = SimulatedVacuum(clock=clock)
    a = await _call(_vacuum(sim, clock=clock), "vacuum_clean_rooms", rooms=["kitchen", "  the HALLWAY "])
    assert a["success"]
    assert a["say"] == "Started cleaning Kitchen and Hallway, battery 100 percent."
    assert a["rooms"] == ["Kitchen", "Hallway"]
    assert a["state"] == "cleaning"
    assert sim.sent == ["clean_rooms:2,3"]


async def test_clean_rooms_goes_through_them_and_home():
    clock = Clock()
    sim = SimulatedVacuum(clock=clock)
    v = _vacuum(sim, clock=clock)
    await v.clean_rooms(["Hallway", "Kitchen", "hallway"])
    assert sim.sent == ["clean_rooms:3,2"]
    assert (await v.where())["room"] == "Hallway"
    clock.now += 31
    assert (await v.where())["room"] == "Kitchen"
    clock.now += 31
    assert (await v.status())["state"] == "returning"


async def test_an_unknown_room_is_refused():
    sim = SimulatedVacuum()
    a = await _vacuum(sim).clean_rooms(["Kitchen", "Bathroom"])
    assert not a["success"]
    assert a["say"] == "There is no room called 'Bathroom'. Choose from Living room, Kitchen and Hallway."
    assert sim.sent == []


async def test_no_rooms_given_asks_which():
    sim = SimulatedVacuum()
    a = await _vacuum(sim).clean_rooms([" "])
    assert not a["success"]
    assert a["say"].startswith("Which rooms? Say one or more of Living room")
    assert sim.sent == []


async def test_a_room_clean_while_cleaning_is_refused():
    sim = SimulatedVacuum()
    v = _vacuum(sim)
    await v.start()
    a = await v.clean_rooms(["Kitchen"])
    assert not a["success"]
    assert "Stop it first" in a["say"]
    assert sim.sent == ["start_clean"]


async def test_clean_rooms_on_a_map_without_rooms():
    fake = FakeClient()
    fake.map_answer = _map(room_list=[], robot_room=None, docked=True)
    a = await _vacuum(fake).clean_rooms(["Room 1"])
    assert not a["success"]
    assert "no rooms" in a["say"]


async def test_a_room_clean_without_a_map_sends_nothing():
    a = await _vacuum(FakeClient()).clean_rooms(["Room 1"])
    assert not a["success"]
    assert "The vacuum wasn't told anything." in a["say"]


async def test_clean_rooms_refused_by_the_cloud():
    fake = FakeClient()
    fake.map_answer = _map(room_list=[{"id": 3, "name": "Room 3"}], robot_room=None, docked=True)
    a = await _vacuum(fake).clean_rooms(["room 3"])
    assert not a["success"]
    assert "didn't confirm the command" in a["say"]


async def test_clean_rooms_offline():
    a = await _vacuum(SimulatedVacuum(offline=True)).clean_rooms(["Kitchen"])
    assert not a["success"]
    assert "The vacuum wasn't told anything." in a["say"]
