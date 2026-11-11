"""Fakes shared by the server.py tests: no robot, no cloud, no network.

Not a test module (no `test_` prefix); imported by test_server_api.py and
test_server_tools.py.
"""

from __future__ import annotations

from dreame_mcp.client import DreameStatus
from dreame_mcp.state import _state


class FakeVacuum:
    """Stands in for DreameHomeClient."""

    def __init__(
        self,
        *,
        ip=None,
        username=None,
        password=None,
        did="42",
        cloud_error=None,
        connected=True,
        local_ready=False,
        connect_ok=True,
        auth_key=None,
    ):
        self._ip = ip
        self._username = username
        self._password = password
        self._did = did
        self.cloud_error = cloud_error
        self.connected = connected
        self.auth_key = auth_key
        self._local_ready = local_ready
        self._connect_ok = connect_ok
        self.disconnects = 0
        self.status: DreameStatus | Exception = DreameStatus(
            state="charging", battery=72, fan_speed="1", is_charging=True, is_cleaning=False
        )
        self.map: dict | Exception = {"success": True, "map_data": {"rooms": 3}}
        self.control_answer = {"success": True, "message": "sent"}
        self.commands: list[str] = []

    def local_miot_ready(self):
        return self._local_ready

    async def connect(self):
        return self._connect_ok

    def disconnect(self):
        self.disconnects += 1

    async def get_status(self):
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    async def get_map(self):
        if isinstance(self.map, Exception):
            raise self.map
        return self.map

    async def control(self, cmd):
        self.commands.append(cmd)
        return self.control_answer


def isolate_server(monkeypatch, tmp_path):
    """Save the shared state, point the .env at tmp_path, clear the login from
    the environment, and make client_from_env answer None unless a test says
    otherwise. Returns (server module, the tmp .env path, restore function)."""
    from dreame_mcp import server

    saved = dict(_state)
    _state.clear()
    for key in ("DREAME_USER", "DREAME_PASSWORD", "DREAME_COUNTRY", "DREAME_IP"):
        monkeypatch.setenv(key, "placeholder")
        monkeypatch.delenv(key)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(server, "_repo_env_path", lambda: env_file)
    monkeypatch.setattr(server, "client_from_env", lambda _dir=None: None)
    monkeypatch.setattr(server, "_server", None)
    monkeypatch.setattr(server, "_serving_stdio", False)
    monkeypatch.setattr(server, "_credentials_dir", None)

    def restore():
        _state.clear()
        _state.update(saved)

    return server, env_file, restore
