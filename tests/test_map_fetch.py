"""Map fetch: the object name's key, the model IV, a fresh map request, the saved-map fallback.

Offline: the cloud, decoder and renderer are fakes; no robot or map file involved.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import dreame_mcp.client as client_mod
from dreame_mcp.client import DreameHomeClient, _map_aes_iv, _split_object_name


def test_split_object_name_with_key():
    assert _split_object_name("ali_dreame/M/1/1,abcKEY") == ("ali_dreame/M/1/1", "abcKEY")


def test_split_object_name_without_key():
    assert _split_object_name(" ali_dreame/M/1/9 ") == ("ali_dreame/M/1/9", None)


def test_no_iv_without_model():
    assert _map_aes_iv(None) is None
    assert _map_aes_iv("dreame.vacuum.nosuchmodel") is None


def test_iv_for_a_known_model():
    ref = os.environ.get("DREAME_REF_PATH")
    try:
        client_mod._bootstrap_protocol(Path(ref) if ref else None)
    except Exception:
        pytest.skip("reference clone not available")
    client_mod._IV_CACHE.clear()
    assert _map_aes_iv("dreame.vacuum.r2363a")


class FakeDecoder:
    calls: list[tuple] = []

    @classmethod
    def decode_map(cls, raw, version, rotation, iv, key):
        cls.calls.append((raw, version, rotation, iv, key))
        return (SimpleNamespace(segments={1: object()}, robot_position=None, charger_position=None),)


class FakeRenderer:
    def render_map(self, map_data, robot_status, station_status):
        return b"png"


@pytest.fixture
def map_client(monkeypatch):
    c = DreameHomeClient(username="u", password="p", did="42")
    c._protocol = object()
    c._model = "dreame.vacuum.test"
    c._map_renderer = FakeRenderer()
    FakeDecoder.calls = []
    monkeypatch.setattr(client_mod, "_map_decoder_cls", FakeDecoder)
    monkeypatch.setattr(client_mod, "_map_aes_iv", lambda model: "IV" if model == "dreame.vacuum.test" else None)
    monkeypatch.setattr(c, "_safe_call", lambda *a, **k: None)
    return c


def test_live_map_fetched_by_name_and_decrypted_with_key(map_client, monkeypatch):
    fetched = []
    monkeypatch.setattr(map_client, "_request_map_object_sync", lambda: "obj/1,KEY")
    monkeypatch.setattr(map_client, "_fetch_map_bytes_from_cloud_url", lambda name: fetched.append(name) or b"RAWMAP")
    result = map_client._get_map_sync()
    assert fetched == ["obj/1"]
    assert FakeDecoder.calls == [("RAWMAP", 1, 0, "IV", "KEY")]
    assert result["success"] and result["source"] == "live"
    assert result["object_name"] == "obj/1"
    assert result["map_data"]["rooms"] == 1


def test_falls_back_to_the_property_name_when_the_request_gives_none(map_client, monkeypatch):
    fetched = []
    monkeypatch.setattr(map_client, "_request_map_object_sync", lambda: None)
    monkeypatch.setattr(map_client, "_resolve_live_map_object_name", lambda: "obj/0,K0")
    monkeypatch.setattr(map_client, "_fetch_map_bytes_from_cloud_url", lambda name: fetched.append(name) or b"RAW")
    map_client._get_map_sync()
    assert fetched == ["obj/0"]
    assert FakeDecoder.calls[0][4] == "K0"


def test_saved_map_when_the_live_map_is_missing(map_client, monkeypatch):
    saved = SimpleNamespace(segments={1: 1, 2: 2}, robot_position=None, charger_position=None)
    monkeypatch.setattr(map_client, "_request_map_object_sync", lambda: "obj/1,KEY")
    monkeypatch.setattr(map_client, "_fetch_map_bytes_from_cloud_url", lambda name: None)
    monkeypatch.setattr(map_client, "_fetch_saved_map_sync", lambda: (saved, "obj/9"))
    result = map_client._get_map_sync()
    assert result["success"] and result["source"] == "saved"
    assert result["object_name"] == "obj/9"
    assert result["map_data"]["rooms"] == 2
    assert result["image"]


def test_failure_when_neither_map_can_be_had(map_client, monkeypatch):
    monkeypatch.setattr(map_client, "_request_map_object_sync", lambda: "obj/1,KEY")
    monkeypatch.setattr(map_client, "_fetch_map_bytes_from_cloud_url", lambda name: None)
    monkeypatch.setattr(map_client, "_fetch_saved_map_sync", lambda: None)
    result = map_client._get_map_sync()
    assert not result["success"]
    assert result["diagnostic_info"]["object_name"] == "obj/1"


@pytest.mark.parametrize(
    ("out", "expected"),
    [
        ([{"piid": 3, "value": "obj/1,KEY"}, {"piid": 13, "value": "1,other,K"}], "obj/1,KEY"),
        ([{"piid": 3, "value": ""}, {"piid": 13, "value": "1,obj/1,KEY"}], "obj/1,KEY"),
        ([{"piid": 13, "value": "0,inline-map-data"}], None),
        ([{"piid": 1, "value": ""}, {"piid": 3, "value": ""}], None),
    ],
)
def test_request_map_reads_the_object_name(out, expected, monkeypatch):
    c = DreameHomeClient(did="42")
    sent = []
    monkeypatch.setattr(c, "_safe_call", lambda *a, **k: sent.append(a) or {"code": 0, "out": out})
    assert c._request_map_object_sync() == expected
    method, verb, payload = sent[0]
    assert (method, verb) == ("send", "action")
    assert (payload["siid"], payload["aiid"]) == (6, 1)


# -- rooms, the robot's room, and what the map tool shows -------------------------


def _decoded(robot_segment=None, docked=None):
    rooms = {
        2: SimpleNamespace(name="Room 2", custom_name="Kitchen"),
        1: SimpleNamespace(name="Room 1", custom_name=None),
    }
    return SimpleNamespace(
        segments=rooms,
        robot_segment=robot_segment,
        docked=docked,
        robot_position=SimpleNamespace(x=10, y=20),
        charger_position=None,
    )


def test_room_list_uses_custom_names_and_finds_the_robot():
    d = client_mod._map_object_to_dict(_decoded(robot_segment=2, docked=False))
    assert d["room_list"] == [{"id": 1, "name": "Room 1"}, {"id": 2, "name": "Kitchen"}]
    assert d["robot_room"] == {"id": 2, "name": "Kitchen"}
    assert d["docked"] is False


def test_robot_outside_every_room():
    d = client_mod._map_object_to_dict(_decoded(robot_segment=0))
    assert d["robot_room"] is None


def test_summary_names_the_rooms_and_where_the_robot_is():
    from dreame_mcp.portmanteau import _format_map_md

    data = {
        "success": True,
        "source": "live",
        "object_name": "o",
        "map_data": client_mod._map_object_to_dict(_decoded(robot_segment=2, docked=True)),
    }
    text = _format_map_md(data)
    assert "Room 1 (id 1), Kitchen (id 2)" in text
    assert "on its dock, in Kitchen (id 2)" in text


def test_summary_of_a_saved_map_does_not_claim_a_position():
    from dreame_mcp.portmanteau import _format_map_md

    data = {
        "success": True,
        "source": "saved",
        "object_name": "o",
        "map_data": client_mod._map_object_to_dict(_decoded(robot_segment=2)),
    }
    text = _format_map_md(data)
    assert "saved" in text
    assert "Robot:" not in text
    assert "Robot Position" not in text


async def test_map_tool_over_mcp_sends_text_and_image(monkeypatch):
    from fastmcp import Client

    from dreame_mcp import server

    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()

    async def fake_map(_client):
        return {"success": True, "source": "live", "object_name": "o", "image": png, "map_data": None}

    monkeypatch.setattr("dreame_mcp.portmanteau.fetch_map_data", fake_map)
    monkeypatch.setitem(server._state, "client", object())
    async with Client(server.mcp) as c:
        result = await c.call_tool("dreame_tool", {"operation": "map"})
        status = await c.call_tool("dreame_tool", {"operation": "battery"})
    assert [b.type for b in result.content] == ["text", "image"]
    assert "LIDAR Map Summary" in result.data
    assert status.structured_content == {"result": status.data}


# -- cleaning rooms -----------------------------------------------------------


def _room_client(monkeypatch, settings, answer=None):
    c = DreameHomeClient(did="42")
    c._protocol = object()
    sent = []

    def call(method, verb, payload):
        sent.append((verb, payload))
        if verb == "get_properties":
            return [{"siid": 4, "piid": p, "code": 0, "value": v} for p, v in settings.items()]
        return {"code": 0} if answer is None else answer

    monkeypatch.setattr(c, "_safe_call", call)
    return c, sent


def test_clean_rooms_sends_start_custom_with_the_rooms(monkeypatch):
    c, sent = _room_client(monkeypatch, {4: 2, 5: 3})
    assert c._clean_rooms_sync([3, 1])["success"]
    verb, payload = sent[1]
    assert verb == "action"
    assert (payload["siid"], payload["aiid"]) == (4, 1)
    status, props = payload["in"]
    assert status == {"piid": 1, "value": 18}
    assert props["piid"] == 10
    assert props["value"] == '{"selects":[[3,1,2,3,1],[1,1,2,3,2]]}'


def test_clean_rooms_on_a_robot_with_per_room_settings(monkeypatch):
    c, sent = _room_client(monkeypatch, {4: 1, 5: 2, 26: 0})
    c._clean_rooms_sync([3, 1])
    assert sent[1][1]["in"][1]["value"] == '{"selects":[[3,1,1,2,1],[1,1,1,2,1]]}'


def test_clean_rooms_refused(monkeypatch):
    c, _ = _room_client(monkeypatch, {4: 1, 5: 2}, answer={"code": -1})
    assert not c._clean_rooms_sync([1])["success"]


def test_clean_rooms_needs_rooms(monkeypatch):
    c, sent = _room_client(monkeypatch, {})
    assert not c._clean_rooms_sync([])["success"]
    assert sent == []
