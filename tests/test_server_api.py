"""The web app's REST API in server.py, pinned down before it is split up.

Offline: the shared client is a fake, every .env lives in tmp_path, and the
Ollama calls go to an httpx MockTransport. No robot, cloud, LLM or port.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image
from server_fakes import FakeVacuum, isolate_server

from dreame_mcp.state import _state


@pytest.fixture
def env(monkeypatch, tmp_path):
    server, env_file, restore = isolate_server(monkeypatch, tmp_path)
    yield SimpleNamespace(server=server, env_file=env_file)
    restore()


@pytest.fixture
def web(env):
    # Not entered as a context manager, so the lifespan (real connect) never runs.
    return TestClient(env.server.app, raise_server_exceptions=False)


def _png_b64(colour=255) -> str:
    buf = io.BytesIO()
    Image.new("L", (2, 1), colour).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# -- health and the reasons control is down ------------------------------------


def test_health_says_unconfigured_when_there_are_no_credentials(web):
    body = web.get("/api/v1/health").json()
    assert body["status"] == "ok"
    assert body["connected"] is False
    assert body["local_miot"] is False
    assert body["mode"] == "unconfigured"
    assert body["control"]["available"] is False
    assert body["control"]["unconfigured"] is True
    assert body["control"]["offline"] is False
    assert "not configured" in body["control"]["reason"]


def test_health_says_offline_and_keeps_the_startup_snapshot_when_connect_failed(web):
    _state["configured"] = True
    _state["connection"] = {"did": "77", "ip": "10.0.0.5", "cloud_error": "bad password"}
    _state["startup_error"] = "it failed"
    body = web.get("/api/v1/health").json()
    assert body["mode"] == "offline"
    assert body["did"] == "77"
    assert body["cloud_error"] == "bad password"
    assert body["startup_error"] == "it failed"
    assert body["control"]["offline"] is True
    assert body["control"]["unconfigured"] is False
    assert "10.0.0.5" in body["control"]["reason"]


def test_health_reports_the_local_path_when_the_robot_answers_miio(web):
    _state["client"] = FakeVacuum(ip="10.0.0.5", local_ready=True)
    body = web.get("/api/v1/health").json()
    assert body["connected"] is True
    assert body["local_miot"] is True
    assert body["mode"] == "local"
    assert body["did"] == "42"
    assert body["control"] == {"available": True, "path": "local (UDP miio)"}


def test_health_reports_the_cloud_path_when_logged_in(web):
    _state["client"] = FakeVacuum(username="u")
    body = web.get("/api/v1/health").json()
    assert body["mode"] == "cloud"
    assert body["control"] == {"available": True, "path": "cloud (DreameHome)"}


def test_mode_is_hybrid_with_both_an_ip_and_a_login(web):
    _state["client"] = FakeVacuum(ip="10.0.0.5", username="u")
    assert web.get("/api/v1/health").json()["mode"] == "hybrid"


def test_control_is_down_with_a_cloud_login_that_is_not_connected(web):
    _state["client"] = FakeVacuum(username="u", connected=False)
    control = web.get("/api/v1/health").json()["control"]
    assert control["available"] is False
    assert "no control path configured" in control["reason"]


def test_control_down_names_the_ip_that_did_not_answer_and_the_cloud_error(web):
    _state["client"] = FakeVacuum(ip="10.0.0.5", username="u", cloud_error="login refused", connected=False)
    control = web.get("/api/v1/health").json()["control"]
    assert control["available"] is False
    assert "10.0.0.5:54321" in control["reason"]
    assert "login refused" in control["reason"]
    assert " | " in control["reason"]


def test_control_down_with_no_path_says_none_is_configured(web):
    _state["client"] = FakeVacuum()
    control = web.get("/api/v1/health").json()["control"]
    assert control == {
        "available": False,
        "reason": "no control path configured (set DREAME_IP or DREAME_USER/DREAME_PASSWORD)",
    }


# -- fixed descriptions -----------------------------------------------------------


def test_capabilities_list_the_tools_and_endpoints(web):
    body = web.get("/api/capabilities").json()
    assert "dreame_tool" in body["capabilities"]["tools"]
    assert body["endpoints"]["map_png"] == "/api/v1/map/png"


def test_diagnostics_list_the_five_tools_and_the_mode(web):
    _state["client"] = FakeVacuum(username="u")
    body = web.get("/api/v1/diagnostics").json()
    assert body["tool_count"] == 5
    assert [t["name"] for t in body["tools"]][-1] == "show_dreame_status_app"
    assert body["system"]["connected"] is True
    assert body["system"]["mode"] == "cloud"


def test_skills_lists_the_shipped_skill_folders(web):
    body = web.get("/api/skills").json()
    assert {"name": "dreame-operator", "uri": "skill://dreame-operator/SKILL.md"} in body["skills"]


def test_skills_is_empty_without_a_skills_folder(web, env, monkeypatch, tmp_path):
    monkeypatch.setattr(env.server, "__file__", str(tmp_path / "server.py"))
    assert web.get("/api/skills").json() == {"skills": []}


def test_map_yaml_is_a_nav2_companion_file(web):
    r = web.get("/api/v1/map/yaml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/yaml")
    assert "dreame_map.pgm" in r.text
    assert "dreame_map.yaml" in r.headers["content-disposition"]


# -- status and map ---------------------------------------------------------------


def test_status_answers_the_robots_reading(web):
    _state["client"] = FakeVacuum()
    r = web.get("/api/v1/status")
    assert r.status_code == 200
    assert r.json()["battery"] == 72


def test_status_is_502_without_a_robot(web):
    r = web.get("/api/v1/status")
    assert r.status_code == 502
    assert r.json()["success"] is False


def test_status_is_500_with_the_error_when_reading_crashes(web):
    vac = FakeVacuum()
    vac.status = RuntimeError("boom")
    _state["client"] = vac
    r = web.get("/api/v1/status")
    assert r.status_code == 500
    assert r.json() == {"success": False, "error": "boom"}


def test_map_answers_the_clients_map(web):
    _state["client"] = FakeVacuum()
    r = web.get("/api/v1/map")
    assert r.status_code == 200
    assert r.json()["map_data"] == {"rooms": 3}


def test_map_is_502_without_a_robot(web):
    r = web.get("/api/v1/map")
    assert r.status_code == 502
    assert "Disconnected" in r.json()["error"]


def test_map_is_500_with_the_error_when_fetching_crashes(web):
    vac = FakeVacuum()
    vac.map = RuntimeError("boom")
    _state["client"] = vac
    r = web.get("/api/v1/map")
    assert r.status_code == 500
    assert r.json() == {"success": False, "error": "boom"}


def test_map_png_is_the_rendered_image(web):
    vac = FakeVacuum()
    png = _png_b64()
    vac.map = {"success": True, "image": png}
    _state["client"] = vac
    r = web.get("/api/v1/map/png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content == base64.b64decode(png)
    assert "dreame_map.png" in r.headers["content-disposition"]


def test_map_png_is_502_without_a_robot(web):
    r = web.get("/api/v1/map/png")
    assert r.status_code == 502
    assert "Disconnected" in r.json()["detail"]


def test_map_png_is_404_without_a_rendered_image(web):
    _state["client"] = FakeVacuum()
    r = web.get("/api/v1/map/png")
    assert r.status_code == 404


def test_an_unexpected_crash_answers_500_json_not_a_bad_gateway(web):
    vac = FakeVacuum()
    vac.map = RuntimeError("kaput")
    _state["client"] = vac
    r = web.get("/api/v1/map/png")
    assert r.status_code == 500
    assert r.json() == {
        "success": False,
        "error": "Internal Server Error",
        "detail": "kaput",
        "service": "dreame-mcp",
    }


def test_map_pgm_turns_white_into_free_and_black_into_occupied(web):
    buf = io.BytesIO()
    img = Image.new("L", (3, 1))
    img.putdata([255, 0, 128])
    img.save(buf, format="PNG")
    vac = FakeVacuum()
    vac.map = {"success": True, "image": base64.b64encode(buf.getvalue()).decode()}
    _state["client"] = vac
    r = web.get("/api/v1/map/pgm")
    assert r.status_code == 200
    assert "dreame_map.pgm" in r.headers["content-disposition"]
    # free -> 254, occupied -> 0, unknown -> 205
    assert r.content == b"P5\n3 1\n255\n" + bytes([254, 0, 205])


def test_map_pgm_is_502_without_a_robot(web):
    r = web.get("/api/v1/map/pgm")
    assert r.status_code == 502


def test_map_pgm_is_404_without_an_image(web):
    _state["client"] = FakeVacuum()
    r = web.get("/api/v1/map/pgm")
    assert r.status_code == 404
    assert "PGM" in r.json()["detail"]


def test_map_pgm_is_404_when_the_image_cannot_be_read(web):
    vac = FakeVacuum()
    vac.map = {"success": True, "image": base64.b64encode(b"not a png").decode()}
    _state["client"] = vac
    assert web.get("/api/v1/map/pgm").status_code == 404


# -- control and shutdown ---------------------------------------------------------


def test_control_sends_a_known_command(web):
    vac = FakeVacuum()
    _state["client"] = vac
    r = web.post("/api/v1/control/go_home")
    assert r.status_code == 200
    assert r.json() == {"success": True, "message": "sent"}
    assert vac.commands == ["go_home"]


def test_control_refuses_an_unknown_command_without_sending_it(web):
    vac = FakeVacuum()
    _state["client"] = vac
    r = web.post("/api/v1/control/self_destruct")
    assert r.status_code == 400
    assert "Unknown command" in r.json()["detail"]
    assert vac.commands == []


def test_control_is_502_with_the_clients_error_when_it_fails(web):
    vac = FakeVacuum()
    vac.control_answer = {"success": False, "error": "robot asleep"}
    _state["client"] = vac
    r = web.post("/api/v1/control/stop")
    assert r.status_code == 502
    assert r.json()["detail"] == "robot asleep"


def test_shutdown_endpoint_disconnects_and_asks_uvicorn_to_exit(web, env, monkeypatch):
    vac = FakeVacuum()
    _state["client"] = vac
    uv = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(env.server, "_server", uv)
    r = web.post("/api/v1/shutdown")
    assert r.json() == {"success": True, "message": "Dreame MCP shutting down"}
    assert vac.disconnects == 1
    assert _state["client"] is None
    assert uv.should_exit is True


# -- connection settings: read -----------------------------------------------------


def test_connection_get_never_shows_the_password_only_whether_it_is_set(web, monkeypatch):
    monkeypatch.setenv("DREAME_PASSWORD", "s3cret")
    monkeypatch.setenv("DREAME_COUNTRY", "")
    body = web.get("/api/v1/connection").json()
    assert "s3cret" not in str(body)
    assert body["password_set"] is True
    assert body["user_set"] is False
    assert body["country"] == "eu"
    assert body["mode"] == "unconfigured"


def test_connection_get_with_a_live_client_shows_its_ip_did_and_login(web, monkeypatch):
    monkeypatch.setenv("DREAME_COUNTRY", "us")
    _state["client"] = FakeVacuum(ip="10.0.0.5", username="u", cloud_error="slow")
    body = web.get("/api/v1/connection").json()
    assert body["ip"] == "10.0.0.5"
    assert body["did"] == "42"
    assert body["user_set"] is True
    assert body["password_set"] is False
    assert body["country"] == "us"
    assert body["cloud_error"] == "slow"
    assert body["connected"] is True


def test_connection_get_without_a_client_shows_the_startup_snapshot(web):
    _state["configured"] = True
    _state["connection"] = {"ip": "10.0.0.9", "did": "9", "cloud_error": "nope"}
    body = web.get("/api/v1/connection").json()
    assert (body["ip"], body["did"], body["cloud_error"], body["configured"]) == ("10.0.0.9", "9", "nope", True)


# -- connection settings: dry-run test ----------------------------------------------


class _Probe:
    """Records how api_connection_test built its throwaway client."""

    made: list[_Probe] = []
    connect_ok = True
    connect_raises: Exception | None = None
    disconnect_raises: Exception | None = None
    cloud_error: str | None = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self._ip = kwargs["ip"]
        self._username = kwargs["username"]
        self._did = "probe-did"
        self.disconnected = False
        type(self).made.append(self)

    async def connect(self):
        if self.connect_raises:
            raise self.connect_raises
        return self.connect_ok

    def disconnect(self):
        self.disconnected = True
        if self.disconnect_raises:
            raise self.disconnect_raises


@pytest.fixture
def probe(env, monkeypatch):
    cls = type("Probe", (_Probe,), {"made": []})
    monkeypatch.setattr(env.server, "DreameHomeClient", cls)
    return cls


def test_connection_test_merges_the_form_over_the_env_file(web, env, probe):
    env.env_file.write_text(
        "# a comment\n\nnot a setting\n"
        " DREAME_USER = file@example.com \n"
        "DREAME_PASSWORD=filepass\n"
        "DREAME_IP=10.0.0.1\n"
        "DREAME_TOKEN=tok\nDREAME_DID=d1\nDREAME_AUTH_KEY=ak\nDREAME_REF_PATH=/ref\n",
        encoding="utf-8",
    )
    r = web.post("/api/v1/connection/test", json={"ip": " 10.0.0.2 ", "user": "  ", "password": None, "country": "us"})
    assert r.json() == {"success": True, "mode": "hybrid", "did": "probe-did"}
    (made,) = probe.made
    assert made.kwargs == {
        "username": "file@example.com",
        "password": "filepass",
        "country": "us",
        "ip": "10.0.0.2",
        "token": "tok",
        "did": "d1",
        "auth_key": "ak",
        "ref_path": Path("/ref"),
    }
    assert made.disconnected is True


def test_connection_test_with_nothing_set_uses_eu_and_none(web, probe):
    r = web.post("/api/v1/connection/test", json={})
    assert r.json()["success"] is True
    assert r.json()["mode"] == "cloud"
    assert probe.made[0].kwargs == {
        "username": None,
        "password": None,
        "country": "eu",
        "ip": None,
        "token": None,
        "did": None,
        "auth_key": None,
        "ref_path": None,
    }


def test_connection_test_never_writes_the_env_file_or_touches_the_live_client(web, env, probe):
    live = FakeVacuum()
    _state["client"] = live
    web.post("/api/v1/connection/test", json={"user": "a@b.c", "password": "pw"})
    assert not env.env_file.exists()
    assert _state["client"] is live
    assert live.disconnects == 0


def test_connection_test_refuses_unknown_keys(web, probe):
    r = web.post("/api/v1/connection/test", json={"token": "x"})
    assert r.status_code == 400
    assert "token" in r.json()["detail"]
    assert probe.made == []


async def test_connection_test_refuses_a_body_that_is_not_an_object(env, probe):
    with pytest.raises(HTTPException) as err:
        await env.server.api_connection_test(["ip"])
    assert err.value.status_code == 400
    assert probe.made == []


def test_connection_test_failure_names_the_ip_and_the_cloud_error(web, probe):
    probe.connect_ok = False
    probe.cloud_error = "wrong password"
    r = web.post("/api/v1/connection/test", json={"ip": "10.0.0.2", "user": "u"})
    assert r.json() == {
        "success": False,
        "error": "no miio answer at 10.0.0.2:54321 | wrong password",
        "did": "probe-did",
    }


def test_connection_test_failure_with_only_an_ip_names_the_ip(web, probe):
    probe.connect_ok = False
    r = web.post("/api/v1/connection/test", json={"ip": "10.0.0.2"})
    assert r.json()["error"] == "no miio answer at 10.0.0.2:54321"


def test_connection_test_failure_with_only_a_cloud_error_names_it(web, probe):
    probe.connect_ok = False
    probe.cloud_error = "wrong password"
    r = web.post("/api/v1/connection/test", json={"user": "u"})
    assert r.json()["error"] == "wrong password"


def test_connection_test_failure_without_a_reason_says_connect_failed(web, probe):
    probe.connect_ok = False
    r = web.post("/api/v1/connection/test", json={})
    assert r.json() == {"success": False, "error": "connect failed", "did": "probe-did"}


def test_connection_test_ignores_a_disconnect_that_fails(web, probe):
    probe.disconnect_raises = RuntimeError("already gone")
    r = web.post("/api/v1/connection/test", json={})
    assert r.json()["success"] is True
    assert probe.made[0].disconnected is True


def test_connection_test_disconnects_even_when_connect_crashes(web, probe):
    probe.connect_raises = RuntimeError("socket")
    r = web.post("/api/v1/connection/test", json={})
    assert r.status_code == 500
    assert r.json()["detail"] == "socket"
    assert probe.made[0].disconnected is True


# -- connection settings: save and reconnect ------------------------------------------


@pytest.fixture
def reconnect(env, monkeypatch):
    """client_from_env hands out this fake, and records the folder it was given."""
    fresh = FakeVacuum(username="new@example.com")
    calls: list = []

    def fake_from_env(credentials_dir=None):
        calls.append(credentials_dir)
        return fresh

    monkeypatch.setattr(env.server, "client_from_env", fake_from_env)
    return SimpleNamespace(client=fresh, calls=calls)


def test_connection_update_saves_reconnects_and_answers_the_new_snapshot(web, env, reconnect, monkeypatch):
    import os

    monkeypatch.setattr(env.server, "_credentials_dir", "/secrets")
    old = FakeVacuum()
    _state["client"] = old
    env.env_file.write_text("# keep me\nDREAME_COUNTRY=eu\n", encoding="utf-8")
    r = web.post(
        "/api/v1/connection",
        json={"ip": " 10.0.0.3 ", "user": "new@example.com", "password": "pw", "country": "us"},
    )
    body = r.json()
    assert body["updated"] == ["DREAME_COUNTRY", "DREAME_IP", "DREAME_PASSWORD", "DREAME_USER"]
    assert body["env_file"] == str(env.env_file)
    assert body["connection"]["connected"] is True
    assert "pw" not in str(body)
    text = env.env_file.read_text(encoding="utf-8")
    assert "# keep me" in text
    assert "DREAME_IP=10.0.0.3" in text
    assert "DREAME_COUNTRY=us" in text
    assert os.environ["DREAME_IP"] == "10.0.0.3"
    assert os.environ["DREAME_USER"] == "new@example.com"
    assert old.disconnects == 1
    assert reconnect.calls == ["/secrets"]
    assert _state["client"] is reconnect.client


def test_connection_update_leaves_an_empty_login_or_password_unchanged(web, env, reconnect):
    env.env_file.write_text("DREAME_USER=old@example.com\nDREAME_PASSWORD=oldpw\n", encoding="utf-8")
    r = web.post("/api/v1/connection", json={"user": " ", "password": "", "ip": "", "country": None})
    assert r.json()["updated"] == ["DREAME_IP"]
    text = env.env_file.read_text(encoding="utf-8")
    assert "DREAME_USER=old@example.com" in text
    assert "DREAME_PASSWORD=oldpw" in text
    assert "DREAME_IP=\n" in text


def test_connection_update_with_nothing_to_change_writes_nothing(web, env, reconnect):
    r = web.post("/api/v1/connection", json={"password": "", "country": None})
    assert r.json()["updated"] == []
    assert r.json()["connection"]["mode"] == "unconfigured"
    assert not env.env_file.exists()
    assert reconnect.calls == []


def test_connection_update_refuses_unknown_keys(web, env, reconnect):
    r = web.post("/api/v1/connection", json={"ip": "10.0.0.3", "DREAME_TOKEN": "x"})
    assert r.status_code == 400
    assert "DREAME_TOKEN" in r.json()["detail"]
    assert not env.env_file.exists()


async def test_connection_update_refuses_a_body_that_is_not_an_object(env, reconnect):
    with pytest.raises(HTTPException) as err:
        await env.server.api_connection_update(["ip"])
    assert err.value.status_code == 400
    assert not env.env_file.exists()


def test_connection_update_is_500_when_the_env_file_cannot_be_written(web, env, reconnect, monkeypatch):
    def broken(_values):
        raise OSError("read-only")

    monkeypatch.setattr(env.server, "update_dotenv", broken)
    r = web.post("/api/v1/connection", json={"ip": "10.0.0.3"})
    assert r.status_code == 500
    assert "read-only" in r.json()["detail"]
    assert reconnect.calls == []


def test_connection_update_reconnects_even_if_the_old_client_fails_to_disconnect(web, reconnect):
    class Sticky(FakeVacuum):
        def disconnect(self):
            raise RuntimeError("stuck")

    _state["client"] = Sticky()
    r = web.post("/api/v1/connection", json={"ip": "10.0.0.3"})
    assert r.status_code == 200
    assert _state["client"] is reconnect.client


def test_connection_update_reports_offline_when_the_new_settings_do_not_connect(web, reconnect):
    reconnect.client._connect_ok = False
    reconnect.client.cloud_error = "wrong password"
    r = web.post("/api/v1/connection", json={"user": "x@example.com"})
    conn = r.json()["connection"]
    assert conn["mode"] == "offline"
    assert conn["connected"] is False
    assert "wrong password" in conn["startup_error"]
    assert _state["client"] is None


# -- startup and shutdown of the web app ------------------------------------------------


def test_the_web_app_connects_on_startup_and_disconnects_on_shutdown(env, monkeypatch):
    vac = FakeVacuum(ip="10.0.0.5", username="u", auth_key="k")
    monkeypatch.setattr(env.server, "client_from_env", lambda _dir=None: vac)
    with TestClient(env.server.app) as web:
        assert _state["client"] is vac
        assert _state["configured"] is True
        assert _state["startup_error"] is None
        assert _state["connection"] == {
            "configured": True,
            "ip": "10.0.0.5",
            "did": "42",
            "has_cloud_creds": False,
            "cloud_error": None,
        }
        assert web.get("/api/v1/health").json()["mode"] == "hybrid"
    assert vac.disconnects == 1


def test_startup_without_credentials_records_not_configured(env):
    with TestClient(env.server.app):
        assert _state["client"] is None
        assert _state["configured"] is False
        assert _state["connection"]["configured"] is False
        assert "No credentials" in _state["startup_error"]


def test_startup_with_a_robot_that_does_not_answer_records_why(env, monkeypatch):
    vac = FakeVacuum(ip="10.0.0.5", username="u", password="p", connect_ok=False, cloud_error="cloud down")
    monkeypatch.setattr(env.server, "client_from_env", lambda _dir=None: vac)
    with TestClient(env.server.app):
        assert _state["client"] is None
        assert _state["connection"]["has_cloud_creds"] is True
        assert "10.0.0.5" in _state["startup_error"]
        assert "cloud down" in _state["startup_error"]
    assert vac.disconnects == 0


# -- the .env writer ---------------------------------------------------------------------


def test_update_dotenv_adds_a_newline_before_appending_to_a_file_without_one(tmp_path):
    from dreame_mcp.server import update_dotenv

    env = tmp_path / ".env"
    env.write_text("DREAME_IP=1", encoding="utf-8")
    update_dotenv({"DREAME_USER": "u"}, path=env)
    assert env.read_text(encoding="utf-8") == "DREAME_IP=1\nDREAME_USER=u\n"


def test_update_dotenv_still_writes_when_the_backup_fails(tmp_path, monkeypatch, caplog):
    from dreame_mcp.server import update_dotenv

    env = tmp_path / ".env"
    env.write_text("DREAME_IP=1\n", encoding="utf-8")

    def no_backup(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", no_backup)
    update_dotenv({"DREAME_IP": "2"}, path=env)
    assert env.read_text(encoding="utf-8") == "DREAME_IP=2\n"
    assert "Could not back up" in caplog.text


# -- chat through Ollama (faked) -----------------------------------------------------------


@pytest.fixture
def ollama(env, monkeypatch):
    """Route the server's httpx calls to a MockTransport handler set per test."""
    seen: list[httpx.Request] = []
    state = SimpleNamespace(handler=None, seen=seen)
    real_client = httpx.AsyncClient

    def handler(request):
        seen.append(request)
        return state.handler(request)

    def fake_client(timeout=None):
        return real_client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(env.server, "httpx", SimpleNamespace(AsyncClient=fake_client))
    return state


def test_llm_providers_lists_the_ollama_models(web, ollama):
    ollama.handler = lambda req: httpx.Response(200, json={"models": [{"name": "a"}, {"name": "b"}]})
    assert web.get("/api/llm/providers").json() == {"providers": [{"name": "ollama", "models": ["a", "b"]}]}
    assert ollama.seen[0].url.path == "/api/tags"


def test_llm_providers_lists_no_models_when_ollama_is_down(web, ollama):
    ollama.handler = lambda req: httpx.Response(503)
    assert web.get("/api/llm/providers").json() == {"providers": [{"name": "ollama", "models": []}]}


def test_llm_chat_needs_a_prompt(web, ollama):
    assert web.post("/api/llm/chat", json={}).json() == {"error": "Missing prompt"}
    assert ollama.seen == []


def test_llm_chat_answers_ollamas_response_with_the_default_model(web, ollama):
    ollama.handler = lambda req: httpx.Response(200, json={"response": "hello"})
    assert web.post("/api/llm/chat", json={"prompt": "hi"}).json() == {"response": "hello"}
    import json

    sent = json.loads(ollama.seen[0].content)
    assert sent == {"model": "llama3.2:3b", "prompt": "hi", "stream": False}
    assert ollama.seen[0].url.path == "/api/generate"


def test_llm_chat_answers_the_error_when_ollama_fails(web, ollama):
    ollama.handler = lambda req: httpx.Response(500)
    body = web.post("/api/llm/chat", json={"prompt": "hi", "model": "m"}).json()
    assert "500" in body["error"]
