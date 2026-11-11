"""How the cloud client finds, downloads and decodes the map, and when it falls back to the saved one.

Offline: the cloud endpoints, decoder and renderer are fakes; no robot or map file involved.
"""

from __future__ import annotations

import base64
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

import dreame_mcp.client as client_mod
from dreame_mcp.client import DreameHomeClient, _map_bytes_looks_like_error_json, _map_raw_bytes_to_str

_REF = "custom_components.dreame_vacuum.dreame"


class Endpoint:
    """A cloud or protocol whose methods answer from a table (an Exception is raised)."""

    def __init__(self, logged_in=True, **answers):
        self._logged_in = logged_in
        self.calls: list = []
        for name, answer in answers.items():
            setattr(self, name, self._method(name, answer))

    def _method(self, name, answer):
        def method(*args):
            self.calls.append((name, args))
            if isinstance(answer, Exception):
                raise answer
            return answer

        return method


# -- which object name the live map has -------------------------------------------------


@pytest.fixture
def ref_names(monkeypatch):
    """The reference's property names, as fakes: OBJECT_NAME is read under the key 'map_value'."""
    const = ModuleType(f"{_REF}.const")
    const.MAP_PARAMETER_VALUE = "map_value"  # type: ignore[attr-defined]
    types = ModuleType(f"{_REF}.types")
    types.DIID = lambda prop: ("diid", prop)  # type: ignore[attr-defined]
    types.DreameVacuumProperty = SimpleNamespace(OBJECT_NAME="OBJECT_NAME")  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, f"{_REF}.const", const)
    monkeypatch.setitem(sys.modules, f"{_REF}.types", types)


def _resolver(protocol=None, cloud=None):
    c = DreameHomeClient()
    c._protocol = protocol
    c._dreame_cloud = cloud
    return c


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ([{"map_value": "obj/1,KEY"}], "obj/1,KEY"),
        ([{"value": " obj/2 "}], "obj/2"),
        ([{"value": ["obj/3", 1]}], "obj/3"),
        ([{"value": '["obj/4", 2]'}], "obj/4"),
        ([{"value": "[]"}], "[]"),
    ],
    ids=["reference-key", "plain-value", "list", "json-list", "empty-json-list"],
)
def test_the_live_map_name_is_read_from_the_object_name_property(ref_names, answer, expected):
    cloud = Endpoint(get_properties=answer)
    assert _resolver(Endpoint(get_properties=None), cloud)._resolve_live_map_object_name() == expected
    assert cloud.calls == [("get_properties", (("diid", "OBJECT_NAME"),))]


def test_the_protocol_is_asked_when_the_cloud_fails(ref_names):
    c = _resolver(Endpoint(get_properties=[{"value": "local/1"}]), Endpoint(get_properties=RuntimeError("401")))
    assert c._resolve_live_map_object_name() == "local/1"


def test_a_cloud_that_is_not_logged_in_is_not_asked(ref_names):
    cloud = Endpoint(logged_in=False, get_properties=[{"value": "cloud/1"}])
    assert _resolver(Endpoint(get_properties=[{"value": "local/1"}]), cloud)._resolve_live_map_object_name() == (
        "local/1"
    )
    assert cloud.calls == []


def test_an_unusable_property_value_falls_back_to_the_object_name_attribute(ref_names):
    proto = Endpoint(get_properties=[{"value": 7}])
    proto.object_name = " attr/1 "  # type: ignore[attr-defined]
    assert _resolver(proto, Endpoint(get_properties=[]))._resolve_live_map_object_name() == "attr/1"


def test_no_name_anywhere_gives_none(ref_names):
    assert _resolver(Endpoint(get_properties=None))._resolve_live_map_object_name() is None
    assert _resolver()._resolve_live_map_object_name() is None


# -- where map files are downloaded from ---------------------------------------------


def test_file_endpoints_are_the_dreame_cloud_then_the_protocols_cloud_then_the_protocol():
    cloud, legacy = Endpoint(), Endpoint()
    proto = SimpleNamespace(cloud=legacy)
    assert _resolver(proto, cloud)._dreame_file_endpoints() == [cloud, legacy, proto]


def test_file_endpoints_without_a_protocol_or_logged_in_cloud():
    cloud = Endpoint()
    assert _resolver(None, cloud)._dreame_file_endpoints() == [cloud]
    proto = SimpleNamespace(cloud=None)
    assert _resolver(proto, Endpoint(logged_in=False))._dreame_file_endpoints() == [proto]


def _downloader(*endpoints):
    c = DreameHomeClient()
    c._dreame_file_endpoints = lambda: list(endpoints)  # type: ignore[method-assign]
    return c


@pytest.mark.parametrize(
    "interim",
    ["https://signed/1", {"url": "https://signed/1"}, {"fileUrl": "https://signed/1"}],
    ids=["text", "url-key", "fileUrl-key"],
)
def test_the_map_is_downloaded_from_the_interim_signed_url(interim):
    ep = Endpoint(get_interim_file_url=interim, get_file=b"RAW", get_file_url={"url": "https://other"})
    assert _downloader(ep)._fetch_map_bytes_from_cloud_url("obj/1") == b"RAW"
    assert ep.calls == [("get_interim_file_url", ("obj/1",)), ("get_file", ("https://signed/1",))]


@pytest.mark.parametrize(
    "interim",
    ["not-a-url", {"url": 5}, RuntimeError("404")],
    ids=["not-a-url", "url-not-text", "fails"],
)
def test_the_file_url_is_asked_when_the_interim_url_is_unusable(interim):
    ep = Endpoint(get_interim_file_url=interim, get_file_url={"fileUrl": " https://signed/2 "}, get_file=b"RAW")
    assert _downloader(ep)._fetch_map_bytes_from_cloud_url("obj/1") == b"RAW"
    assert ("get_file_url", ("/obj/1",)) in ep.calls
    assert ep.calls[-1] == ("get_file", ("https://signed/2",))


def test_a_file_url_given_as_text_is_used_and_a_leading_slash_is_kept():
    ep = Endpoint(get_file_url="https://signed/3", get_file=b"RAW")
    assert _downloader(ep)._fetch_map_bytes_from_cloud_url("/obj/1") == b"RAW"
    assert ep.calls[0] == ("get_file_url", ("/obj/1",))


def test_the_next_endpoint_is_tried_when_one_gives_no_map():
    no_url = Endpoint(get_file_url=RuntimeError("401"))
    text_not_url = Endpoint(get_file_url="nope")
    no_get_file = Endpoint(get_file_url={"url": "https://signed/4"})
    get_file_fails = Endpoint(get_file_url={"url": "https://signed/4"}, get_file=RuntimeError("403"))
    error_body = Endpoint(get_file_url={"url": "https://signed/4"}, get_file=b'{"code": 80001}')
    good = Endpoint(get_file_url={"url": "https://signed/4"}, get_file=b"RAW")
    c = _downloader(no_url, text_not_url, no_get_file, get_file_fails, error_body, good)
    assert c._fetch_map_bytes_from_cloud_url("obj/1") == b"RAW"
    assert error_body.calls[-1] == ("get_file", ("https://signed/4",))


def test_no_endpoint_gives_a_map():
    assert (
        _downloader(
            Endpoint(), Endpoint(get_file_url={"url": "https://x"}, get_file=b"")
        )._fetch_map_bytes_from_cloud_url("obj/1")
        is None
    )


# -- the saved map ------------------------------------------------------------------


class SavedDecoder:
    calls: list = []

    @classmethod
    def decode_saved_map(cls, raw, version, angle, key):
        cls.calls.append((raw, version, angle, key))
        return None if raw.startswith("bad") else SimpleNamespace(map_id=int(raw[-1]), segments={})


@pytest.fixture
def saved_client(monkeypatch):
    c = DreameHomeClient(did="42")
    c._protocol = object()
    SavedDecoder.calls = []
    monkeypatch.setattr(client_mod, "_map_decoder_cls", SavedDecoder)
    sent: list = []
    answers = {"list": [{"value": json.dumps({"object_name": "list/8"})}], "file": None}

    def call(*args):
        sent.append(args)
        return answers["list"]

    monkeypatch.setattr(c, "_safe_call", call)
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: answers["file"])
    return c, answers, sent


def _map_list(current, *maps):
    return json.dumps({"curr_id": current, "mapstr": list(maps)}).encode()


def test_the_current_saved_map_is_chosen(saved_client):
    c, answers, sent = saved_client
    answers["file"] = _map_list(2, {"map": "map1", "angle": "90"}, {"map": "map2"}, {"map": "map3"})
    mdl, name = c._fetch_saved_map_sync()  # type: ignore[misc]
    assert (mdl.map_id, name) == (2, "list/8")
    assert SavedDecoder.calls == [("map1", 1, 90, None), ("map2", 1, 0, None), ("map3", 1, 0, None)]
    assert sent == [("send", "get_properties", [{"did": "42", "siid": 6, "piid": 8}])]


def test_the_first_decodable_saved_map_is_used_when_none_is_current(saved_client):
    c, answers, _ = saved_client
    answers["file"] = _map_list(9, {"map": "bad1"}, {"map": "map2"}, {"map": "map3"})
    mdl, _ = c._fetch_saved_map_sync()  # type: ignore[misc]
    assert mdl.map_id == 2


def test_an_undecodable_map_after_a_good_one_is_skipped(saved_client):
    c, answers, _ = saved_client
    answers["file"] = json.dumps({"mapstr": [{"map": "map2"}, {"map": "bad1"}]}).encode()
    mdl, _ = c._fetch_saved_map_sync()  # type: ignore[misc]
    assert mdl.map_id == 2


def test_no_saved_map_when_none_decodes(saved_client):
    c, answers, _ = saved_client
    answers["file"] = _map_list(1, {"map": "bad1"})
    assert c._fetch_saved_map_sync() is None
    answers["file"] = json.dumps({"curr_id": 1}).encode()
    assert c._fetch_saved_map_sync() is None


@pytest.mark.parametrize(
    "listing",
    [None, [], [{"value": 5}], [{"value": "not json"}], [{"value": "{}"}]],
    ids=["no-answer", "empty", "not-text", "not-json", "no-name"],
)
def test_no_saved_map_without_a_map_list_name(saved_client, listing):
    c, answers, _ = saved_client
    answers["list"] = listing
    answers["file"] = _map_list(2, {"map": "map2"})
    assert c._fetch_saved_map_sync() is None


def test_no_saved_map_when_the_list_cannot_be_downloaded(saved_client):
    c, _, _ = saved_client
    assert c._fetch_saved_map_sync() is None


def test_no_saved_map_without_a_decoder(saved_client, monkeypatch):
    c, _, sent = saved_client
    monkeypatch.setattr(client_mod, "_map_decoder_cls", None)
    assert c._fetch_saved_map_sync() is None
    assert sent == []


def test_a_saved_map_that_fails_gives_no_answer(monkeypatch):
    c = DreameHomeClient()

    def broken():
        raise ValueError("bad list")

    monkeypatch.setattr(c, "_fetch_saved_map_sync", broken)
    assert c._saved_map_result() is None


def test_a_saved_map_without_a_renderer_has_rooms_but_no_picture(monkeypatch):
    c = DreameHomeClient()
    monkeypatch.setattr(c, "_fetch_saved_map_sync", lambda: (SimpleNamespace(segments={1: 1}), "obj/9"))
    result = c._saved_map_result()
    assert result is not None
    assert "image" not in result
    assert result["map_data"]["rooms"] == 1


# -- getting the map ----------------------------------------------------------------


class Decoder:
    calls: list = []
    answer: object = None

    @classmethod
    def decode_map(cls, raw, version, rotation, iv, key):
        cls.calls.append((raw, iv, key))
        if isinstance(cls.answer, Exception):
            raise cls.answer
        return cls.answer


class Renderer:
    def __init__(self, picture=b"png"):
        self.picture = picture

    def render_map(self, map_data, robot_status, station_status):
        return self.picture


def _decoded(rooms=1):
    return SimpleNamespace(segments={i: i for i in range(1, rooms + 1)})


@pytest.fixture
def map_client(monkeypatch):
    """A connected client whose cloud answers come from the `answers` table, by method name."""
    c = DreameHomeClient(did="42")
    c._protocol = SimpleNamespace(_country="de")
    c._model = "dreame.vacuum.test"
    c._map_renderer = Renderer()
    Decoder.calls = []
    Decoder.answer = (_decoded(),)
    monkeypatch.setattr(client_mod, "_map_decoder_cls", Decoder)
    monkeypatch.setattr(client_mod, "_map_renderer_cls", Renderer)
    monkeypatch.setattr(client_mod, "_map_aes_iv", lambda model: "IV" if model == "dreame.vacuum.test" else None)
    monkeypatch.setattr(c, "_request_map_object_sync", lambda: "obj/1,KEY")
    monkeypatch.setattr(c, "_resolve_live_map_object_name", lambda: None)
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: None)
    monkeypatch.setattr(c, "_fetch_saved_map_sync", lambda: None)
    answers: dict = {}
    calls: list = []

    def call(method, *args):
        calls.append((method, *args))
        answer = answers.get(method)
        if callable(answer):
            return answer(*args)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(c, "_safe_call", call)
    return c, answers, calls


def test_no_map_without_a_connection():
    assert DreameHomeClient()._get_map_sync() == {"success": False, "error": "Not connected"}


def test_a_live_map_comes_with_its_raw_bytes(map_client, monkeypatch):
    c, _, calls = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    result = c._get_map_sync()
    assert (result["source"], result["raw_bytes"], result["image"]) == ("live", 3, base64.b64encode(b"png").decode())
    assert result["raw_b64"] == base64.b64encode(b"RAW").decode()
    assert calls == []


def test_a_cloud_error_body_is_not_taken_as_a_map(map_client, monkeypatch):
    c, answers, calls = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b'{"code": 80001}')
    answers["get_device_file"] = b"FILE"
    result = c._get_map_sync()
    assert result["success"] and result["object_name"] == "obj/1"
    assert Decoder.calls == [("FILE", "IV", "KEY")]
    assert calls == [("get_device_file", "obj/1", "map")]


def test_the_device_file_is_tried_as_map_then_as_type_zero(map_client):
    c, answers, calls = map_client
    answers["get_device_file"] = lambda name, kind: b"FILE0" if kind == 0 else b'{"success": false}'
    assert c._get_map_sync()["raw_bytes"] == 5
    assert calls == [("get_device_file", "obj/1", "map"), ("get_device_file", "obj/1", 0)]


def test_the_map_property_is_read_when_no_file_comes(map_client):
    c, answers, calls = map_client
    answers["get_properties"] = [{"value": base64.b64encode(b"PROP").decode()}]
    result = c._get_map_sync()
    assert result["object_name"] == "property_23_1"
    assert Decoder.calls[0][0] == "PROP"
    assert calls[-1] == ("get_properties", [{"did": "42", "siid": 23, "piid": 1}])


def test_the_file_url_is_the_last_resort(map_client):
    c, answers, calls = map_client
    answers["get_properties"] = RuntimeError("no property 23.1")
    answers["get_file_url"] = {"fileUrl": " https://signed/5 "}
    answers["get_file"] = b"URLMAP"
    result = c._get_map_sync()
    assert result["success"] and result["raw_bytes"] == 6
    assert calls[-2:] == [("get_file_url", "/obj/1"), ("get_file", "https://signed/5")]


def test_an_empty_map_property_and_an_unusable_file_url_fail(map_client):
    c, answers, calls = map_client
    answers["get_properties"] = [{"value": ""}]
    answers["get_file_url"] = {"url": "  "}
    result = c._get_map_sync()
    assert result["success"] is False
    assert "get_file" not in [m for m, *_ in calls]


def test_a_failing_file_url_ends_in_the_failure_answer(map_client):
    c, answers, _ = map_client
    answers["get_file_url"] = RuntimeError("401")
    result = c._get_map_sync()
    assert result["success"] is False
    assert "diagnostic_info" in result


def test_the_failure_lists_every_step_tried_and_the_region(map_client):
    c, _, _ = map_client
    result = c._get_map_sync()
    assert result["success"] is False
    diag = result["diagnostic_info"]
    assert (diag["did"], diag["object_name"], diag["country"]) == ("42", "obj/1", "de")
    assert diag["attempted"] == [
        {"step": "get_device_file:map", "object_name": "obj/1"},
        {"step": "get_device_file:0", "object_name": "obj/1"},
    ]


def test_the_region_comes_from_the_protocols_cloud_when_the_protocol_has_none(map_client):
    c, _, _ = map_client
    c._protocol = SimpleNamespace(cloud=SimpleNamespace(_country="sg"))
    assert c._get_map_sync()["diagnostic_info"]["country"] == "sg"


def test_without_an_object_name_only_the_map_property_is_tried(map_client, monkeypatch):
    c, _, calls = map_client
    monkeypatch.setattr(c, "_request_map_object_sync", lambda: None)
    result = c._get_map_sync()
    assert result["diagnostic_info"]["object_name"] is None
    assert [m for m, *_ in calls] == ["get_properties"]


def test_a_saved_map_answers_when_no_live_map_comes(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_saved_map_sync", lambda: (_decoded(2), "obj/9"))
    result = c._get_map_sync()
    assert (result["source"], result["object_name"], result["map_data"]["rooms"]) == ("saved", "obj/9", 2)


def test_a_decoder_that_returns_the_map_itself_is_accepted(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    Decoder.answer = _decoded(3)
    assert c._get_map_sync()["map_data"]["rooms"] == 3


def test_no_decode_result_counts_as_undecryptable(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    Decoder.answer = None
    result = c._get_map_sync()
    assert result["source"] == "live" and "map_data" not in result
    assert "wrong key" in result["render_error"]
    assert result["raw_b64"] == base64.b64encode(b"RAW").decode()


def test_a_decoder_crash_falls_back_to_the_saved_map(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    monkeypatch.setattr(c, "_fetch_saved_map_sync", lambda: (_decoded(), "obj/9"))
    Decoder.answer = ValueError("padding is incorrect")
    result = c._get_map_sync()
    assert result["source"] == "saved"
    assert result["live_error"] == "padding is incorrect"


def test_a_render_that_gives_no_picture_still_gives_the_rooms(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    c._map_renderer = Renderer(picture=b"")
    result = c._get_map_sync()
    assert "image" not in result and result["map_data"]["rooms"] == 1


def test_the_model_iv_comes_from_the_cloud_when_the_client_has_no_model(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    c._model = None
    c._dreame_cloud = SimpleNamespace(_model="dreame.vacuum.test", _logged_in=False)
    c._get_map_sync()
    assert Decoder.calls[0][1] == "IV"


def test_the_map_managers_iv_is_used_when_the_model_has_none(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    c._model = "dreame.vacuum.unknown"
    c._map_manager = SimpleNamespace(_aes_iv="MANAGER-IV")
    c._get_map_sync()
    assert Decoder.calls[0][1] == "MANAGER-IV"


def test_without_the_map_decoder_the_raw_map_is_still_returned(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    monkeypatch.setattr(client_mod, "_map_decoder_cls", None)
    result = c._get_map_sync()
    assert result["success"] and result["render_error"] == "Map PNG unavailable: Tasshack map import"
    assert result["raw_b64"] == base64.b64encode(b"RAW").decode()


def test_without_a_renderer_the_answer_says_it_was_not_set_up(map_client, monkeypatch):
    c, _, _ = map_client
    monkeypatch.setattr(c, "_fetch_map_bytes_from_cloud_url", lambda name: b"RAW")
    c._map_renderer = None
    assert c._get_map_sync()["render_error"] == "Map PNG unavailable: renderer init"
    monkeypatch.setattr(client_mod, "_map_renderer_cls", None)
    assert c._get_map_sync()["render_error"] == "Map PNG unavailable: Tasshack map import"


def test_an_unexpected_error_becomes_a_failed_answer(map_client, monkeypatch):
    c, _, _ = map_client

    def broken():
        raise RuntimeError("cloud gone")

    monkeypatch.setattr(c, "_request_map_object_sync", broken)
    assert c._get_map_sync() == {"success": False, "error": "cloud gone"}


# -- asking the robot for a fresh map ------------------------------------------------


def test_a_failing_map_request_gives_no_name(monkeypatch):
    c = DreameHomeClient(did="42")

    def broken(*a):
        raise RuntimeError("gateway 400")

    monkeypatch.setattr(c, "_safe_call", broken)
    assert c._request_map_object_sync() is None


def test_a_map_request_without_an_answer_gives_no_name(monkeypatch):
    c = DreameHomeClient(did="42")
    monkeypatch.setattr(c, "_safe_call", lambda *a: None)
    assert c._request_map_object_sync() is None


# -- map bytes -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "is_error"),
    [
        (None, False),
        (b"{", False),
        (b"RAWMAP", False),
        (b"{not json}", False),
        (b'{"a": "\xff"}', False),
        (b'{"success": false}', True),
        (b'{"code": 0}', False),
        (b'{"code": "0"}', False),
        (b'{"code": 80001}', True),
        (b'{"success": true}', False),
    ],
    ids=["none", "one-byte", "map", "not-json", "not-utf8", "success-false", "code-0", "code-text-0", "code", "ok"],
)
def test_a_cloud_error_body_is_told_apart_from_a_map(raw, is_error):
    assert _map_bytes_looks_like_error_json(raw) is is_error


def test_map_bytes_that_are_not_utf8_are_read_as_latin1():
    assert _map_raw_bytes_to_str(b"caf\xe9") == "café"
    assert _map_raw_bytes_to_str("café".encode()) == "café"
