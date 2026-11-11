"""The MCP server's tools, resource and prompts in server.py and agentic.py,
and the command line that starts it.

Offline: fastmcp's in-memory Client, a fake shared client, a fake sampling
context, and fakes for uvicorn and the stdio loop. No robot, cloud, LLM or port.
"""

from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace

import pytest
from fastmcp import Client
from server_fakes import FakeVacuum, isolate_server

from dreame_mcp import agentic
from dreame_mcp.client import DreameStatus
from dreame_mcp.state import _state


@pytest.fixture
def env(monkeypatch, tmp_path):
    server, env_file, restore = isolate_server(monkeypatch, tmp_path)
    yield SimpleNamespace(server=server, env_file=env_file)
    restore()


async def _call(server, tool, args=None):
    async with Client(server.mcp) as c:
        return await c.call_tool(tool, args or {})


def _card_text(result) -> str:
    return str(result.structured_content)


# -- dreame_help -------------------------------------------------------------------


async def test_help_without_a_category_lists_them_and_the_connection(env):
    _state["client"] = FakeVacuum(ip="10.0.0.5")
    out = (await _call(env.server, "dreame_help")).structured_content
    assert out["connected"] is True
    assert out["mode"] == "local"
    assert out["did"] == "42"
    assert set(out["categories"]) == {"status", "map", "control", "connection", "agentic"}


async def test_help_offline_takes_the_did_from_the_startup_snapshot(env):
    _state["configured"] = True
    _state["connection"] = {"did": "77"}
    out = (await _call(env.server, "dreame_help")).structured_content
    assert (out["connected"], out["mode"], out["did"]) == (False, "offline", "77")


async def test_help_for_a_category_gives_its_detail(env):
    out = (await _call(env.server, "dreame_help", {"category": "map"})).structured_content
    assert out == {"category": "map", "detail": env.server._HELP_CATEGORIES["map"]}


async def test_help_for_an_unknown_category_lists_the_known_ones(env):
    out = (await _call(env.server, "dreame_help", {"category": "lasers"})).structured_content
    assert out["error"] == "Unknown category: lasers"
    assert "status" in out["available"]


# -- dreame_shutdown -----------------------------------------------------------------


async def test_shutdown_disconnects_and_asks_uvicorn_to_exit(env, monkeypatch):
    vac = FakeVacuum()
    _state["client"] = vac
    uv = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(env.server, "_server", uv)
    out = (await _call(env.server, "dreame_shutdown")).structured_content
    assert out == {"success": True, "message": "Dreame MCP shutting down"}
    assert vac.disconnects == 1
    assert _state["client"] is None
    assert uv.should_exit is True


async def test_shutdown_over_stdio_schedules_an_exit_half_a_second_later(env, monkeypatch):
    scheduled = []
    loop = SimpleNamespace(call_later=lambda *a: scheduled.append(a))
    monkeypatch.setattr(env.server, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    monkeypatch.setattr(env.server, "_serving_stdio", True)
    await env.server.dreame_shutdown()
    assert scheduled == [(0.5, sys.exit, 0)]


async def test_shutdown_with_neither_server_only_clears_the_client(env, monkeypatch):
    scheduled = []
    loop = SimpleNamespace(call_later=lambda *a: scheduled.append(a))
    monkeypatch.setattr(env.server, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    _state["client"] = None
    out = await env.server.dreame_shutdown()
    assert out["success"] is True
    assert scheduled == []


# -- show_dreame_status_app ----------------------------------------------------------


async def test_status_card_unconfigured_says_how_to_configure(env):
    result = await _call(env.server, "show_dreame_status_app")
    assert result.content[0].text.startswith("Robot not configured — Set DREAME_USER")
    assert "Not configured" in _card_text(result)


async def test_status_card_offline_gives_the_reasons(env):
    _state["configured"] = True
    _state["connection"] = {"ip": "10.0.0.5"}
    result = await _call(env.server, "show_dreame_status_app")
    assert result.content[0].text.startswith("Robot offline — local path down")
    assert "10.0.0.5" in _card_text(result)


async def test_status_card_says_when_fetching_the_status_crashes(env):
    vac = FakeVacuum()
    vac.status = RuntimeError("timeout")
    _state["client"] = vac
    result = await _call(env.server, "show_dreame_status_app")
    assert result.content[0].text == "Status fetch failed: timeout"


async def test_status_card_shows_the_robots_error(env):
    vac = FakeVacuum()
    vac.status = DreameStatus(error="no answer")
    _state["client"] = vac
    result = await _call(env.server, "show_dreame_status_app")
    assert result.content[0].text == "Status unavailable: no answer"
    assert "no answer" in _card_text(result)


async def test_status_card_shows_the_live_reading(env):
    _state["client"] = FakeVacuum()
    result = await _call(env.server, "show_dreame_status_app")
    text = result.content[0].text
    assert "- State: charging" in text
    assert "- Battery: 72%" in text
    assert "- Charging: YES" in text
    assert "- Cleaning: IDLE" in text
    assert "- Fan speed: 1" in text
    card = _card_text(result)
    assert "Live status" in card
    assert "72%" in card


async def test_status_card_while_cleaning_says_active_and_not_charging(env):
    vac = FakeVacuum()
    vac.status = DreameStatus(state="sweeping", battery=50, is_cleaning=True, is_charging=False)
    _state["client"] = vac
    text = (await _call(env.server, "show_dreame_status_app")).content[0].text
    assert "- Charging: NO" in text
    assert "- Cleaning: ACTIVE" in text


# -- the dreame://status resource ------------------------------------------------------


async def _resource(server) -> str:
    async with Client(server.mcp) as c:
        return (await c.read_resource("dreame://status"))[0].text


async def test_status_resource_unconfigured(env):
    assert (await _resource(env.server)).startswith("**Robot:** not configured")


async def test_status_resource_offline_gives_the_reasons(env):
    _state["configured"] = True
    _state["connection"] = {"cloud_error": "bad login"}
    assert await _resource(env.server) == "**Robot offline:** cloud path down: bad login"


async def test_status_resource_shows_the_robots_error(env):
    vac = FakeVacuum()
    vac.status = DreameStatus(error="no answer")
    _state["client"] = vac
    assert await _resource(env.server) == "**Status unavailable:** no answer"


async def test_status_resource_shows_the_live_reading(env):
    _state["client"] = FakeVacuum()
    assert await _resource(env.server) == (
        "**State:** charging | **Battery:** 72% | **Charging:** yes | **Cleaning:** idle"
    )


async def test_status_resource_while_cleaning(env):
    vac = FakeVacuum()
    vac.status = DreameStatus(state="sweeping", battery=50, is_cleaning=True)
    _state["client"] = vac
    assert (await _resource(env.server)).endswith("**Charging:** no | **Cleaning:** active")


# -- prompts -------------------------------------------------------------------------------


async def test_prompts_give_setup_and_diagnostic_steps(env):
    async with Client(env.server.mcp) as c:
        quick = await c.get_prompt("dreame_quick_start")
        diag = await c.get_prompt("dreame_diagnostics")
    assert "DREAME_USER" in quick.messages[0].content.text
    assert "/api/v1/health" in diag.messages[0].content.text


async def test_the_server_offers_its_named_tools(env):
    async with Client(env.server.mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert {
        "dreame_tool",
        "dreame_help",
        "dreame_agentic_workflow",
        "dreame_shutdown",
        "show_dreame_status_app",
    } <= names


# -- dreame_agentic_workflow --------------------------------------------------------------


class _Ctx:
    def __init__(self, answer=None, error=None):
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    async def sample(self, prompt):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return self.answer


async def test_agentic_workflow_sends_the_goal_and_status_and_answers_the_plan(env):
    _state["client"] = FakeVacuum()
    ctx = _Ctx(answer=SimpleNamespace(text="Cleaned the kitchen."))
    out = await agentic.dreame_agentic_workflow(goal="clean the kitchen", ctx=ctx)  # type: ignore[arg-type]
    assert out == "Cleaned the kitchen."
    (prompt,) = ctx.prompts
    assert "Current robot status: battery=72%, state=charging, charging=True" in prompt
    assert "Goal: clean the kitchen" in prompt
    assert "dreame_tool(operation='go_home')" in prompt


async def test_agentic_workflow_without_a_robot_marks_the_status_unknown(env):
    ctx = _Ctx(answer=SimpleNamespace(text="ok"))
    await agentic.dreame_agentic_workflow(goal="g", ctx=ctx)  # type: ignore[arg-type]
    assert "battery=?%, state=?, charging=?" in ctx.prompts[0]


async def test_agentic_workflow_goes_on_when_the_status_crashes(env, monkeypatch):
    async def broken(_client):
        raise RuntimeError("cloud")

    monkeypatch.setattr(agentic, "fetch_status_data", broken)
    ctx = _Ctx(answer=SimpleNamespace(text="ok"))
    await agentic.dreame_agentic_workflow(goal="g", ctx=ctx)  # type: ignore[arg-type]
    assert ctx.prompts[0].startswith("Current robot status: status unavailable\n")


async def test_agentic_workflow_answers_the_whole_result_when_it_has_no_text(env):
    ctx = _Ctx(answer="plain answer")
    assert await agentic.dreame_agentic_workflow(goal="g", ctx=ctx) == "plain answer"  # type: ignore[arg-type]


async def test_agentic_workflow_says_it_failed_when_sampling_is_unavailable(env):
    ctx = _Ctx(error=RuntimeError("no sampling"))
    assert await agentic.dreame_agentic_workflow(goal="g", ctx=ctx) == "Workflow failed: no sampling"  # type: ignore[arg-type]


# -- the command line ------------------------------------------------------------------------


class _FakeUvicorn:
    def __init__(self):
        self.configs: list[dict] = []
        self.servers: list = []
        fake = self

        class Config:
            def __init__(self, app, **kw):
                fake.configs.append({"app": app, **kw})

        class Server:
            def __init__(self, config):
                self.ran = False
                fake.servers.append(self)

            def run(self):
                self.ran = True

        self.Config = Config
        self.Server = Server


@pytest.fixture
def fake_uvicorn(env, monkeypatch):
    fake = _FakeUvicorn()
    monkeypatch.setattr(env.server, "uvicorn", fake)
    return fake


def test_main_serves_http_on_localhost_and_the_default_port(env, fake_uvicorn, monkeypatch):
    monkeypatch.delenv("DREAME_MCP_PORT", raising=False)
    monkeypatch.delenv("DREAME_MCP_HOST", raising=False)
    monkeypatch.delenv("DREAME_SECRETS_DIR", raising=False)
    monkeypatch.setattr(sys, "argv", ["dreame-mcp"])
    env.server.main()
    (config,) = fake_uvicorn.configs
    assert config["app"] is env.server.app
    assert (config["host"], config["port"]) == ("127.0.0.1", 10894)
    assert fake_uvicorn.servers[0].ran is True
    assert env.server._server is fake_uvicorn.servers[0]
    assert env.server._credentials_dir is None


def test_main_takes_the_host_port_and_secrets_folder_from_the_environment(env, fake_uvicorn, monkeypatch):
    monkeypatch.setenv("DREAME_MCP_PORT", "1234")
    monkeypatch.setenv("DREAME_MCP_HOST", "0.0.0.0")  # noqa: S104 - only handed to the fake uvicorn
    monkeypatch.setenv("DREAME_SECRETS_DIR", "/run/secrets/dreame")
    monkeypatch.setattr(sys, "argv", ["dreame-mcp", "--mode", "http"])
    env.server.main()
    assert (fake_uvicorn.configs[0]["host"], fake_uvicorn.configs[0]["port"]) == ("0.0.0.0", 1234)  # noqa: S104
    assert env.server._credentials_dir == "/run/secrets/dreame"


def test_main_in_stdio_mode_connects_serves_stdio_and_disconnects(env, fake_uvicorn, monkeypatch):
    vac = FakeVacuum()
    given = []

    def from_env(credentials_dir=None):
        given.append(credentials_dir)
        return vac

    served = []

    async def fake_stdio():
        served.append((_state.get("client"), env.server._serving_stdio))

    monkeypatch.setattr(env.server, "client_from_env", from_env)
    monkeypatch.setattr(env.server.mcp, "run_stdio_async", fake_stdio)
    monkeypatch.setattr(sys, "argv", ["dreame-mcp", "--mode", "stdio", "--secrets-dir", "/s"])
    env.server.main()
    assert given == ["/s"]
    assert served == [(vac, True)]
    assert vac.disconnects == 1
    assert fake_uvicorn.configs == []


def test_stdio_disconnects_even_when_serving_crashes(env, monkeypatch):
    import asyncio

    vac = FakeVacuum()

    async def crash():
        raise RuntimeError("pipe closed")

    monkeypatch.setattr(env.server, "client_from_env", lambda _dir=None: vac)
    monkeypatch.setattr(env.server.mcp, "run_stdio_async", crash)
    with pytest.raises(RuntimeError, match="pipe closed"):
        asyncio.run(env.server._run_stdio())
    assert vac.disconnects == 1


def test_python_dash_m_dreame_mcp_runs_main(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.server, "main", lambda: calls.append(1))
    runpy.run_module("dreame_mcp.__main__", run_name="__main__")
    assert calls == [1]
