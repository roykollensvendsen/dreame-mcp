"""The web app takes commands that move the robot or stop the server only from its own pages.

Programs that are not browsers (curl, scripts) are accepted as before.
Offline: the client is a fake, the server never stops.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dreame_mcp import server
from dreame_mcp.state import _state

ROUTES = ["/api/v1/control/find_robot", "/api/v1/shutdown"]


class Fake:
    connected = True

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def control(self, cmd: str) -> dict:
        self.sent.append(cmd)
        return {"success": True, "message": f"Sent {cmd}"}

    def disconnect(self) -> None:
        pass


@pytest.fixture
def web(monkeypatch):
    fake = Fake()
    monkeypatch.setitem(_state, "client", fake)
    monkeypatch.setattr(server, "_server", None)
    monkeypatch.setattr(server, "_serving_stdio", False)
    yield TestClient(server.app), fake


@pytest.mark.parametrize("route", ROUTES)
def test_a_control_request_from_another_site_is_refused(web, route):
    client, fake = web
    answer = client.post(route, headers={"Origin": "https://other.example", "Sec-Fetch-Site": "cross-site"})
    assert answer.status_code == 403
    assert fake.sent == []
    assert _state["client"] is fake


@pytest.mark.parametrize("route", ROUTES)
def test_another_sites_referer_is_refused_when_there_is_no_origin(web, route):
    client, fake = web
    answer = client.post(route, headers={"Referer": "https://other.example/page"})
    assert answer.status_code == 403
    assert fake.sent == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_browser_request_without_an_origin_is_refused(web, route):
    client, fake = web
    answer = client.post(route, headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"})
    assert answer.status_code == 403
    assert fake.sent == []


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:10895", "http://localhost:10894", "tauri://localhost", "http://robot.tail-ab12.ts.net:10895"],
)
def test_the_apps_own_pages_may_send_a_command(web, origin):
    client, fake = web
    answer = client.post("/api/v1/control/find_robot", headers={"Origin": origin, "Sec-Fetch-Site": "same-site"})
    assert answer.status_code == 200
    assert fake.sent == ["find_robot"]


def test_the_apps_own_referer_is_enough_without_an_origin(web):
    client, fake = web
    answer = client.post("/api/v1/control/find_robot", headers={"Referer": "http://localhost:10895/#/control"})
    assert answer.status_code == 200
    assert fake.sent == ["find_robot"]


def test_a_program_that_is_not_a_browser_may_still_send_a_command(web):
    client, fake = web
    assert client.post("/api/v1/control/find_robot").status_code == 200
    assert fake.sent == ["find_robot"]


def test_the_null_origin_of_a_sandboxed_page_is_refused(web):
    client, fake = web
    assert client.post("/api/v1/shutdown", headers={"Origin": "null"}).status_code == 403
    assert _state["client"] is fake
