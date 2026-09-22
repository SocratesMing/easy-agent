"""MCP 工具加载：单个 server 失效不得拖垮其他 server。

背景：早先的实现是 `MultiServerMCPClient(整个 config).get_tools()`，任何一个
server 连不上都会整体抛异常并被 except 吞掉，用户所有 MCP 工具一起消失
（日志里只有一行 "Failed to load MCP tools"）。这里把该行为钉死。

全部用例不联网：`MultiServerMCPClient` 被替换为按 server 名决定行为的假实现。
"""

from __future__ import annotations

import pytest

from easy_agent.services import mcp as mcp_mod


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeClient:
    """按 server 名决定成功/失败的假 MCP 客户端。"""

    calls: list[str] = []
    behaviors: dict[str, Exception] = {}

    def __init__(self, config: dict) -> None:
        (self._name,) = config.keys()

    async def get_tools(self) -> list[_FakeTool]:
        type(self).calls.append(self._name)
        failure = type(self).behaviors.get(self._name)
        if failure:
            raise failure
        return [_FakeTool(f"{self._name}_tool")]


def _install(monkeypatch, tmp_path, servers: dict) -> None:
    """把配置来源与 MCP 客户端都换成假的，并清掉模块级缓存。"""
    cfg = tmp_path / "mcp.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_mod, "_resolve_mcp_config_path", lambda username=None: cfg)
    monkeypatch.setattr(mcp_mod, "load_mcp_config", lambda username=None: servers)
    monkeypatch.setattr(mcp_mod, "MultiServerMCPClient", _FakeClient)
    mcp_mod._mcp_tools_cache.clear()
    mcp_mod._server_failure_until.clear()
    _FakeClient.calls = []
    _FakeClient.behaviors = {}


def _servers(*names: str) -> dict:
    return {n: {"transport": "sse", "url": f"http://127.0.0.1:9000/{n}"} for n in names}


# ── 失败隔离 ───────────────────────────────────────────────────────────


async def test_broken_server_does_not_hide_healthy_ones(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    _FakeClient.behaviors = {"bad": RuntimeError("connection refused")}

    tools = await mcp_mod.get_mcp_tools()

    assert [t.name for t in tools] == ["good_tool"]


async def test_all_servers_broken_returns_empty_without_raising(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _servers("a", "b"))
    _FakeClient.behaviors = {
        "a": RuntimeError("boom-a"),
        "b": RuntimeError("boom-b"),
    }

    assert await mcp_mod.get_mcp_tools() == []


async def test_stdio_precheck_skips_without_connecting(monkeypatch, tmp_path):
    """预检就该拦下的配置（命令不存在）不该真去拉子进程。"""
    _install(
        monkeypatch,
        tmp_path,
        {"bad": {"transport": "stdio", "command": "definitely-not-a-real-cmd-xyz"}},
    )

    assert await mcp_mod.get_mcp_tools() == []
    assert _FakeClient.calls == []


# ── 缓存与冷却 ─────────────────────────────────────────────────────────


async def test_healthy_baseline_is_cached(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _servers("good"))

    await mcp_mod.get_mcp_tools()
    await mcp_mod.get_mcp_tools()

    assert _FakeClient.calls == ["good"]


async def test_partial_result_is_not_cached(monkeypatch, tmp_path):
    """有失败就不写缓存，否则 server 恢复后得手改 mcp.json 才能生效。"""
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    _FakeClient.behaviors = {"bad": RuntimeError("boom")}

    await mcp_mod.get_mcp_tools()
    await mcp_mod.get_mcp_tools()

    # 两次都重新加载了健康的 good，说明没命中缓存
    assert _FakeClient.calls.count("good") == 2


async def test_failed_server_is_not_retried_during_cooldown(monkeypatch, tmp_path):
    """冷却期内跳过失败 server，避免每次加载都白等一遍连接超时。"""
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    _FakeClient.behaviors = {"bad": RuntimeError("boom")}

    await mcp_mod.get_mcp_tools()
    assert _FakeClient.calls == ["good", "bad"]

    await mcp_mod.get_mcp_tools()
    assert _FakeClient.calls == ["good", "bad", "good"]


async def test_recovered_server_returns_after_cooldown(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    monkeypatch.setattr(mcp_mod, "MCP_FAILURE_COOLDOWN_SECONDS", 0.0)
    _FakeClient.behaviors = {"bad": RuntimeError("boom")}

    assert [t.name for t in await mcp_mod.get_mcp_tools()] == ["good_tool"]

    _FakeClient.behaviors = {}
    tools = await mcp_mod.get_mcp_tools()
    assert sorted(t.name for t in tools) == ["bad_tool", "good_tool"]


async def test_invalidate_clears_cache_and_cooldown(monkeypatch, tmp_path):
    """用户保存了新的 mcp.json 就该立刻重试，不该继续被上一轮失败压着。"""
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    _FakeClient.behaviors = {"bad": RuntimeError("boom")}

    await mcp_mod.get_mcp_tools()
    _FakeClient.behaviors = {}
    mcp_mod.invalidate_mcp_cache(None)

    tools = await mcp_mod.get_mcp_tools()
    assert sorted(t.name for t in tools) == ["bad_tool", "good_tool"]


# ── 逐 server 校验（前端保存后提示用） ─────────────────────────────────


async def test_validate_reports_each_server_independently(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, _servers("good", "bad"))
    _FakeClient.behaviors = {"bad": RuntimeError("boom")}

    results = await mcp_mod.validate_mcp_servers()
    by_name = {r["name"]: r for r in results}

    assert by_name["good"] == {
        "name": "good",
        "status": "ok",
        "tools_count": 1,
        "error": "",
    }
    assert by_name["bad"]["status"] == "error"
    assert by_name["bad"]["tools_count"] == 0
    assert "boom" in by_name["bad"]["error"]


async def test_validate_returns_empty_without_config(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {})

    assert await mcp_mod.validate_mcp_servers() == []


@pytest.mark.parametrize("username", [None, "alice"])
async def test_no_config_path_short_circuits(monkeypatch, username):
    monkeypatch.setattr(mcp_mod, "_resolve_mcp_config_path", lambda username=None: None)

    assert await mcp_mod.get_mcp_tools(username) == []
