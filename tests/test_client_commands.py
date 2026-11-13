"""How the cloud client reads the status, sends commands, falls back to the cloud and gives up in time.

Offline: the protocol and the Dreame cloud are fakes; no robot or cloud involved.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import dreame_mcp.client as client_mod
from dreame_mcp.client import DreameHomeClient, _map_aes_iv, _miot_did_slots, _pick_cloud_device


def _answering(monkeypatch, c, answer):
    """Make c._safe_call record each call and give `answer` (or raise it)."""
    seen: list = []

    def call(*args, **kw):
        seen.append(args)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(c, "_safe_call", call)
    return seen


def _connected(did="42"):
    c = DreameHomeClient(did=did)
    c._protocol = object()
    return c


# -- status -------------------------------------------------------------------------


def test_no_status_without_a_connection():
    assert DreameHomeClient()._get_status_sync().error == "Not connected"


def test_no_status_when_the_robot_does_not_answer(monkeypatch):
    c = _connected()
    _answering(monkeypatch, c, None)
    assert c._get_status_sync().error == "No response from device"


def test_the_status_is_read_from_its_properties(monkeypatch):
    c = _connected()
    answer = [
        {"siid": 2, "piid": 1, "value": 6},
        {"siid": 3, "piid": 1, "value": 72},
        {"siid": 3, "piid": 2, "value": 1},
        {"siid": 4, "piid": 2, "value": 1800},
        {"siid": 4, "piid": 3, "value": 235000},
        {"siid": 4, "piid": 4, "value": 2},
        {"siid": 2, "piid": 2, "code": -4004},
    ]
    seen = _answering(monkeypatch, c, answer)
    s = c._get_status_sync()
    assert (s.state, s.battery, s.fan_speed) == ("charging", 72, "2")
    assert (s.is_charging, s.is_cleaning) == (True, False)
    assert (s.cleaned_area, s.cleaning_time) == (23.5, 1800)
    assert "2.2" not in s.raw
    method, verb, props = seen[0]
    assert (method, verb) == ("send", "get_properties")
    assert [(p["siid"], p["piid"]) for p in props] == [(2, 1), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2), (4, 3), (4, 4)]
    assert {p["did"] for p in props} == {"42"}


def test_an_unknown_state_is_named_by_its_number(monkeypatch):
    c = _connected(did=None)
    seen = _answering(monkeypatch, c, [{"siid": 2, "piid": 1, "value": 99}])
    s = c._get_status_sync()
    assert s.state == "state_99"
    assert (s.battery, s.cleaned_area, s.cleaning_time, s.is_charging) == (0, 0.0, 0, False)
    assert [p["did"] for p in seen[0][2]] == [str(i) for i in range(8)]


@pytest.mark.parametrize(("code", "cleaning"), [(12, True), (1, True), (11, False), (6, False)])
def test_sweeping_and_mopping_counts_as_cleaning_but_building_does_not(monkeypatch, code, cleaning):
    c = _connected()
    _answering(monkeypatch, c, [{"siid": 2, "piid": 1, "value": code}])
    assert c._get_status_sync().is_cleaning is cleaning


def test_a_status_read_that_fails_gives_the_error(monkeypatch):
    c = _connected()
    _answering(monkeypatch, c, [{"siid": 3, "piid": 1, "value": "full"}])
    assert "full" in (c._get_status_sync().error or "")


# -- commands ----------------------------------------------------------------------


def test_no_command_without_a_connection():
    assert DreameHomeClient()._control_sync("pause") == {"success": False, "error": "Not connected"}


def test_an_unknown_command_is_refused_without_sending(monkeypatch):
    c = _connected()
    seen = _answering(monkeypatch, c, {"code": 0})
    assert c._control_sync("dance") == {"success": False, "error": "Unknown command: dance"}
    assert seen == []


def test_a_command_is_sent_as_its_action(monkeypatch):
    c = _connected()
    seen = _answering(monkeypatch, c, {"code": 0})
    assert c._control_sync("go_home") == {"success": True, "message": "Sent go_home", "result": {"code": 0}}
    assert seen == [("send", "action", {"did": "42", "siid": 3, "aiid": 1, "in": []})]


def test_a_command_without_an_answer_fails(monkeypatch):
    c = _connected()
    _answering(monkeypatch, c, None)
    assert c._control_sync("stop") == {"success": False, "error": "No response for stop"}


def test_a_command_that_raises_fails_with_the_error(monkeypatch):
    c = _connected()
    _answering(monkeypatch, c, RuntimeError("gateway 400"))
    assert c._control_sync("stop") == {"success": False, "error": "gateway 400"}


def test_no_room_clean_without_a_connection():
    assert DreameHomeClient()._clean_rooms_sync([1]) == {"success": False, "error": "Not connected"}


def test_a_room_clean_that_raises_fails_with_the_error(monkeypatch):
    c = _connected()
    _answering(monkeypatch, c, RuntimeError("gateway 400"))
    assert c._clean_rooms_sync([1]) == {"success": False, "error": "gateway 400"}


def test_a_room_clean_uses_default_suction_and_water_when_unknown(monkeypatch):
    c = _connected()
    seen = _answering(monkeypatch, c, None)
    c._clean_rooms_sync([5])
    assert seen[1][2]["in"][1]["value"] == '{"selects":[[5,1,1,2,1]]}'


# -- the cloud fallback ---------------------------------------------------------------


class Endpoint:
    """A protocol or cloud whose methods answer from a table (an Exception is raised)."""

    def __init__(self, logged_in=True, cloud=None, **answers):
        self._logged_in = logged_in
        self.cloud = cloud
        self.calls: list = []
        for name, answer in answers.items():
            setattr(self, name, self._method(name, answer))

    def _method(self, name, answer):
        def method(*args, **kwargs):
            self.calls.append((name, args))
            if isinstance(answer, Exception):
                raise answer
            return answer

        return method


def _client(protocol=None, cloud=None):
    c = DreameHomeClient()
    c._protocol = protocol
    c._dreame_cloud = cloud
    return c


def test_the_local_answer_is_used_when_there_is_one():
    cloud = Endpoint(send="cloud")
    c = _client(Endpoint(send="local"), cloud)
    c._last_protocol_error = "old"
    assert c._safe_call("send", "get_properties", 1) == "local"
    assert c._last_protocol_error is None
    assert cloud.calls == []


def test_a_failing_local_read_is_retried_through_the_cloud():
    cloud = Endpoint(send="cloud")
    c = _client(Endpoint(send=OSError("no route")), cloud)
    assert c._safe_call("send", "get_properties", 1) == "cloud"
    assert cloud.calls == [("send", ("get_properties", 1))]
    assert c._last_protocol_error is None


def test_an_empty_local_answer_is_retried_through_the_cloud():
    c = _client(Endpoint(send=None), Endpoint(send="cloud"))
    assert c._safe_call("send") == "cloud"


def test_a_file_call_skips_a_legacy_cloud_that_is_not_logged_in():
    local = Endpoint(cloud=Endpoint(logged_in=False), get_file="legacy")
    c = _client(local, Endpoint(get_file="dreame"))
    assert c._safe_call("get_file", "https://x") == "dreame"
    assert local.calls == []


def test_a_file_call_the_protocol_lacks_goes_to_its_logged_in_cloud():
    legacy = Endpoint(get_file_url={"url": "https://x"})
    c = _client(Endpoint(cloud=legacy))
    assert c._safe_call("get_file_url", "/o") == {"url": "https://x"}
    assert legacy.calls == [("get_file_url", ("/o",))]


def test_a_method_nobody_has_gives_nothing():
    assert _client(Endpoint(), Endpoint(send="cloud"))._safe_call("send") is None


def test_the_cloud_fallback_needs_a_logged_in_cloud():
    assert _client(Endpoint(send=None), Endpoint(logged_in=False, send="cloud"))._safe_call("send") is None
    assert _client(Endpoint(send=None))._safe_call("send") is None


def test_the_cloud_fallback_needs_the_method():
    c = _client(Endpoint(send=None), Endpoint())
    assert c._safe_call("send") is None
    assert c._last_protocol_error is None


def test_a_failing_cloud_fallback_keeps_the_error():
    c = _client(Endpoint(send=OSError("no route")), Endpoint(send=RuntimeError("cloud 500")))
    assert c._safe_call("send") is None
    assert c._last_protocol_error == "cloud 500"


def test_an_empty_cloud_answer_keeps_the_local_error():
    c = _client(Endpoint(send=OSError("no route")), Endpoint(send=None))
    assert c._safe_call("send") is None
    assert c._last_protocol_error == "no route"


# -- a command reaches the robot once -------------------------------------------------


class Robot:
    """The vacuum itself: counts every action that reaches it."""

    def __init__(self):
        self.actions: list = []


class LostAnswer:
    """The protocol as it really fails (protocol.py, DreameVacuumProtocol.send): the request
    goes out and reaches the robot, the answer is lost, and it raises cloud_unreachable."""

    def __init__(self, robot):
        self.robot = robot
        self.cloud = None

    def send(self, method, parameters=None, retry_count=2, timeout=None):
        if method == "action":
            self.robot.actions.append(parameters)
        raise RuntimeError("cloud_unreachable")


class DreameCloud:
    """The logged-in Dreame-native cloud: it passes the action on and says so."""

    _logged_in = True

    def __init__(self, robot):
        self.robot = robot

    def send(self, method, parameters=None, retry_count=2, timeout=None):
        if method == "action":
            self.robot.actions.append(parameters)
            return {"code": 0}
        return [{"piid": p["piid"], "code": 0, "value": 1} for p in parameters]  # get_properties


def test_a_command_whose_answer_is_lost_reaches_the_robot_once():
    robot = Robot()
    c = _client(LostAnswer(robot), DreameCloud(robot))
    c._did = "42"
    c._control_sync("start_clean")
    assert len(robot.actions) == 1


def test_a_room_clean_whose_answer_is_lost_reaches_the_robot_once():
    robot = Robot()
    c = _client(LostAnswer(robot), DreameCloud(robot))
    c._did = "42"
    c._clean_rooms_sync([3])
    assert len(robot.actions) == 1


class RetryingCloud:
    """The Dreame cloud as its request() really works (protocol.py, request): after a read
    timeout it sends the same request again, up to retry_count more times. The first
    request reaches the robot; its answer is lost."""

    _logged_in = True

    def __init__(self, robot):
        self.robot = robot

    def send(self, method, parameters=None, retry_count=2, timeout=None):
        for attempt in range(retry_count + 1):
            self.robot.actions.append(parameters)
            if attempt:
                return {"code": 0}
        return None


def test_a_command_is_not_sent_again_by_the_clouds_own_retry():
    robot = Robot()
    c = _client(LostAnswer(robot), RetryingCloud(robot))
    c._did = "42"
    c._control_sync("stop")
    assert len(robot.actions) == 1


def test_a_command_goes_through_the_logged_in_dreame_cloud_only():
    local, cloud = Endpoint(send="local"), Endpoint(send="cloud")
    c = _client(local, cloud)
    assert c._safe_call("send", "action", 1) == "cloud"
    assert local.calls == []
    assert cloud.calls == [("send", ("action", 1))]


def test_a_command_the_dreame_cloud_fails_is_not_sent_again_locally():
    local = Endpoint(send="local")
    c = _client(local, Endpoint(send=RuntimeError("cloud 500")))
    assert c._safe_call("send", "action", 1) is None
    assert local.calls == []
    assert c._last_protocol_error == "cloud 500"


def test_a_command_with_no_route_at_all_gives_nothing():
    assert _client(None, Endpoint(logged_in=False, send="cloud"))._send_action_once("action", 1) is None


def test_without_the_dreame_cloud_a_command_goes_locally_once():
    local = Endpoint(send=OSError("no route"))
    cloud = Endpoint(logged_in=False, send="cloud")
    c = _client(local, cloud)
    assert c._safe_call("send", "action", 1) is None
    assert local.calls == [("send", ("action", 1))]
    assert cloud.calls == []


# -- connected, the auth key, disconnecting ------------------------------------------


def test_a_logged_in_cloud_counts_as_connected():
    assert _client(cloud=Endpoint()).connected is True


def test_no_protocol_and_no_cloud_is_not_connected():
    assert _client(cloud=Endpoint(logged_in=False)).connected is False


def test_a_local_link_counts_as_connected():
    assert _client(SimpleNamespace(connected=True)).connected is True


def test_an_ip_counts_as_connected_once_the_protocol_exists():
    c = _client(SimpleNamespace(connected=False))
    assert c.connected is False
    c._ip = "10.0.0.2"
    assert c.connected is True


def test_a_protocol_that_fails_to_say_counts_as_not_connected():
    class Broken:
        @property
        def connected(self):
            raise RuntimeError("socket closed")

    assert _client(Broken()).connected is False


def test_the_auth_key_comes_from_the_dreame_cloud_first():
    c = _client(SimpleNamespace(auth_key="local"), SimpleNamespace(auth_key="dreame"))
    assert c.auth_key == "dreame"


def test_the_auth_key_comes_from_the_protocol_then_its_cloud():
    assert _client(SimpleNamespace(auth_key="local"), SimpleNamespace(auth_key=None)).auth_key == "local"
    assert _client(SimpleNamespace(auth_key=None, cloud=SimpleNamespace(auth_key="legacy"))).auth_key == "legacy"
    assert _client(SimpleNamespace(auth_key=None, cloud=None)).auth_key is None
    assert _client().auth_key is None


def test_disconnecting_drops_the_protocol_cloud_and_map_helpers():
    closed = []
    c = _client(SimpleNamespace(disconnect=lambda: closed.append(1)), Endpoint())
    c._map_manager = c._map_renderer = object()
    c.disconnect()
    assert closed == [1]
    assert (c._protocol, c._dreame_cloud, c._map_manager, c._map_renderer) == (None, None, None, None)


def test_disconnecting_survives_a_protocol_that_fails_to_close():
    def broken():
        raise RuntimeError("already closed")

    c = _client(SimpleNamespace(disconnect=broken))
    c.disconnect()
    assert c._protocol is None


def test_local_miot_is_ready_only_after_the_device_connected():
    assert _client().local_miot_ready() is False
    assert _client(SimpleNamespace(device=None)).local_miot_ready() is False
    assert _client(SimpleNamespace(device=SimpleNamespace(connected=False))).local_miot_ready() is False
    assert _client(SimpleNamespace(device=SimpleNamespace(connected=True))).local_miot_ready() is True


# -- giving up in time -----------------------------------------------------------------


@pytest.fixture
def never_in_time(monkeypatch):
    """Every wait in the client runs out at once, without waiting."""

    async def wait_for(future, timeout):
        future.cancel()
        raise TimeoutError

    monkeypatch.setattr(
        client_mod, "asyncio", SimpleNamespace(get_event_loop=asyncio.get_event_loop, wait_for=wait_for)
    )


async def test_a_login_that_takes_too_long_is_not_connected(never_in_time, monkeypatch):
    c = DreameHomeClient()
    monkeypatch.setattr(c, "_connect_sync", lambda: True)
    assert await c.connect() is False


async def test_a_status_that_takes_too_long_says_so(never_in_time):
    status = await DreameHomeClient().get_status()
    assert "timed out" in (status.error or "")


async def test_a_command_that_takes_too_long_says_so(never_in_time):
    result = await DreameHomeClient().control("pause")
    assert result["success"] is False and "timed out" in result["error"]


async def test_a_room_clean_that_takes_too_long_says_so(never_in_time):
    result = await DreameHomeClient().clean_rooms([1])
    assert result["success"] is False and "timed out" in result["error"]


async def test_a_map_that_takes_too_long_says_so(never_in_time):
    result = await DreameHomeClient().get_map()
    assert result["success"] is False and result["timeout"] is True


async def test_answers_in_time_are_passed_on(monkeypatch):
    c = DreameHomeClient()
    monkeypatch.setattr(c, "_connect_sync", lambda: True)
    monkeypatch.setattr(c, "_control_sync", lambda cmd: {"sent": cmd})
    monkeypatch.setattr(c, "_clean_rooms_sync", lambda rooms: {"rooms": rooms})
    monkeypatch.setattr(c, "_get_map_sync", lambda: {"map": 1})
    assert await c.connect() is True
    assert (await c.get_status()).error == "Not connected"
    assert await c.control("pause") == {"sent": "pause"}
    assert await c.clean_rooms((3, 1)) == {"rooms": [3, 1]}
    assert await c.get_map() == {"map": 1}


# -- small helpers ------------------------------------------------------------------


def test_a_missing_or_none_did_gives_numbered_slots():
    assert _miot_did_slots(3, None) == ["0", "1", "2"]
    assert _miot_did_slots(2, " None ") == ["0", "1"]
    assert _miot_did_slots(2, " 42 ") == ["42", "42"]


def test_a_model_iv_is_remembered(monkeypatch):
    monkeypatch.setitem(client_mod._IV_CACHE, "cachedmodel", "CACHED")
    assert _map_aes_iv("dreame.vacuum.cachedmodel") == "CACHED"


def test_the_device_with_the_ip_is_picked():
    devices = [{"did": 1, "localip": "10.0.0.9"}, {"did": 2, "localip": "10.0.0.2"}]
    assert _pick_cloud_device(devices, "10.0.0.2") == devices[1]


def test_the_only_device_is_picked_even_with_another_ip(caplog):
    devices = [{"did": 1, "localip": "10.0.0.9"}]
    assert _pick_cloud_device(devices, "10.0.0.2") == devices[0]
    assert "set DREAME_DID" in caplog.text


def test_several_devices_without_a_match_pick_none():
    devices = [{"did": 1, "localip": "10.0.0.9"}, {"did": 2, "localip": "10.0.0.8"}]
    assert _pick_cloud_device(devices, "10.0.0.2") is None
    assert _pick_cloud_device(devices, None) is None
