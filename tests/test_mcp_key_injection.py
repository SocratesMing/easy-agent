"""MCP 配置相关测试：自动签发 API Key + 用户 mcp.json 的原子写入。

只测纯逻辑：是否识别出"自家业务"、是否该注入、会不会覆盖用户已有的 Key，
以及写入失败时旧配置是否完好。签发动作本身用 monkeypatch 替换，不连数据库。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

import easy_agent.services.mcp_api_keys as keys_service
from easy_agent.api import settings as settings_api


# ── 业务识别 ────────────────────────────────────────────────────────────


def test_resolve_business_from_url():
    cfg = {"url": "http://127.0.0.1:8100/mcp/dataqa/", "headers": {}}
    assert settings_api._resolve_easy_business(cfg) == "dataqa"


def test_resolve_business_from_url_without_trailing_slash():
    cfg = {"url": "http://127.0.0.1:8100/mcp/market"}
    assert settings_api._resolve_easy_business(cfg) == "market"


def test_resolve_business_from_declared_field():
    """市场条目可显式标注业务，URL 不规范时也能识别。"""
    cfg = {"url": "https://mcp.example.com/gateway", "business": "dataqa"}
    assert settings_api._resolve_easy_business(cfg) == "dataqa"


def test_resolve_business_returns_none_for_foreign_server():
    cfg = {"url": "http://127.0.0.1:8005/sse"}
    assert settings_api._resolve_easy_business(cfg) is None


def test_resolve_business_ignores_unsupported_name():
    cfg = {"business": "not-a-business"}
    assert settings_api._resolve_easy_business(cfg) is None


# ── 是否需要注入 ────────────────────────────────────────────────────────


def test_placeholder_authorization_needs_injection():
    cfg = {"headers": {"Authorization": "Bearer <在设置页生成的 Key>"}}
    assert settings_api._needs_key_injection(cfg) is True


def test_missing_authorization_needs_injection():
    assert settings_api._needs_key_injection({}) is True


def test_real_key_is_not_overwritten():
    cfg = {"headers": {"Authorization": "Bearer mcp_existing_key"}}
    assert settings_api._needs_key_injection(cfg) is False


# ── 注入行为 ────────────────────────────────────────────────────────────


def test_injects_key_for_own_business_with_placeholder(monkeypatch):
    issued = []

    def fake_issue(username: str, business: str) -> str:
        issued.append((username, business))
        return f"mcp_key_{username}_{business}"

    monkeypatch.setattr(keys_service, "issue_api_key", fake_issue)
    cfg = {
        "url": "http://127.0.0.1:8100/mcp/dataqa/",
        "headers": {"Authorization": "Bearer <占位符>"},
    }

    result, business = settings_api._maybe_inject_api_key(cfg, "alice")

    assert business == "dataqa"
    assert issued == [("alice", "dataqa")]
    assert result["headers"]["Authorization"] == "Bearer mcp_key_alice_dataqa"
    # 原配置不应被就地修改
    assert cfg["headers"]["Authorization"] == "Bearer <占位符>"


def test_skips_foreign_server(monkeypatch):
    called = []

    def fake_issue(username: str, business: str) -> str:
        called.append((username, business))
        return "should-not-be-used"

    monkeypatch.setattr(keys_service, "issue_api_key", fake_issue)
    cfg = {"url": "http://127.0.0.1:8005/sse", "headers": {}}

    result, business = settings_api._maybe_inject_api_key(cfg, "alice")

    assert business is None
    assert result is cfg
    assert called == []


def test_keeps_user_supplied_key(monkeypatch):
    monkeypatch.setattr(keys_service, "issue_api_key", lambda u, b: "new-key")
    cfg = {
        "url": "http://127.0.0.1:8100/mcp/dataqa/",
        "headers": {"Authorization": "Bearer mcp_mine"},
    }

    result, business = settings_api._maybe_inject_api_key(cfg, "alice")

    assert business is None
    assert result["headers"]["Authorization"] == "Bearer mcp_mine"


def test_each_user_gets_own_key(monkeypatch):
    monkeypatch.setattr(
        keys_service, "issue_api_key", lambda username, business: f"key_of_{username}"
    )
    cfg = {"url": "http://127.0.0.1:8100/mcp/dataqa/", "headers": {}}

    alice, _ = settings_api._maybe_inject_api_key(dict(cfg), "alice")
    bob, _ = settings_api._maybe_inject_api_key(dict(cfg), "bob")

    assert alice["headers"]["Authorization"] == "Bearer key_of_alice"
    assert bob["headers"]["Authorization"] == "Bearer key_of_bob"


# ── 用户 mcp.json 的原子写入 ────────────────────────────────────────────


def _workdir() -> Path:
    """自建临时目录，不用 pytest 的 tmp_path。

    本机环境里 pytest 清理 tmp_path 会触发插件 shim 的批量删除保护
    （抛 SystemExit），与测试内容无关。这里留下的文件位于系统临时目录，
    由操作系统回收。
    """
    return Path(tempfile.mkdtemp(prefix="easy_agent_mcp_test_"))


def _patch_mcp_path(monkeypatch, target: Path) -> None:
    monkeypatch.setattr(
        settings_api.Config,
        "get_user_mcp_path",
        staticmethod(lambda username, config=None: target),
    )


def test_atomic_write_produces_valid_json(monkeypatch):
    target = _workdir() / "alice" / "mcp.json"
    _patch_mcp_path(monkeypatch, target)
    payload = {"servers": {"dataqa": {"url": "http://127.0.0.1:8100/mcp/dataqa/"}}}

    result = settings_api._write_user_mcp_raw("alice", None, payload)

    assert result == target
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    # 目录里只应有目标文件，临时文件必须被清理
    assert sorted(p.name for p in target.parent.iterdir()) == ["mcp.json"]


def test_atomic_write_overwrites_existing_file(monkeypatch):
    target = _workdir() / "mcp.json"
    target.write_text(json.dumps({"servers": {"old": {}}}), encoding="utf-8")
    _patch_mcp_path(monkeypatch, target)

    settings_api._write_user_mcp_raw("alice", None, {"servers": {"new": {}}})

    assert json.loads(target.read_text(encoding="utf-8")) == {"servers": {"new": {}}}


def test_atomic_write_keeps_old_content_when_replace_fails(monkeypatch):
    """替换失败时必须保留旧配置，且不留下临时文件（否则用户 MCP 配置全废）。"""
    target = _workdir() / "mcp.json"
    original = {"servers": {"old": {}}}
    target.write_text(json.dumps(original), encoding="utf-8")
    _patch_mcp_path(monkeypatch, target)

    # 只让目标文件替换失败，其余 os.replace 调用透传：
    # 全局替换 os.replace 会干扰环境里其他依赖它的代码（如测试框架的清理逻辑）
    real_replace = os.replace

    def selective_replace(src, dst, *args, **kwargs):
        if str(dst) == str(target):
            raise OSError("replace failed")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(settings_api.os, "replace", selective_replace)

    with pytest.raises(OSError):
        settings_api._write_user_mcp_raw("alice", None, {"servers": {"new": {}}})

    assert json.loads(target.read_text(encoding="utf-8")) == original
    assert sorted(p.name for p in target.parent.iterdir()) == ["mcp.json"]


# ── 重签后回写用户配置 ──────────────────────────────────────────────────


def _patch_side_effects(monkeypatch) -> None:
    """隔离配置加载与缓存失效，专注验证回写逻辑。"""
    monkeypatch.setattr(settings_api, "get_agent_config", lambda: None)
    monkeypatch.setattr(settings_api, "invalidate_mcp_cache", lambda username: None)
    monkeypatch.setattr(settings_api, "invalidate_user_agents", lambda username: 0)


def test_sync_updates_matching_server_key(monkeypatch):
    target = _workdir() / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "servers": {
                    "dataqa": {
                        "url": "http://127.0.0.1:8100/mcp/dataqa/",
                        "headers": {"Authorization": "Bearer old_key"},
                    },
                    "akshare": {
                        "url": "http://127.0.0.1:8005/sse",
                        "headers": {"Authorization": "Bearer keep_me"},
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    _patch_mcp_path(monkeypatch, target)
    _patch_side_effects(monkeypatch)

    changed = settings_api._sync_business_key("alice", "dataqa", "mcp_new_key")

    assert changed == 1
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["servers"]["dataqa"]["headers"]["Authorization"] == "Bearer mcp_new_key"
    # 别人的 server 不能被碰
    assert data["servers"]["akshare"]["headers"]["Authorization"] == "Bearer keep_me"


def test_sync_updates_multiple_servers_of_same_business(monkeypatch):
    target = _workdir() / "mcp.json"
    target.write_text(
        json.dumps(
            {
                "servers": {
                    "dataqa": {"url": "http://127.0.0.1:8100/mcp/dataqa/", "headers": {}},
                    "dataqa-2": {"url": "https://mcp.example.com/mcp/dataqa/", "headers": {}},
                }
            }
        ),
        encoding="utf-8",
    )
    _patch_mcp_path(monkeypatch, target)
    _patch_side_effects(monkeypatch)

    assert settings_api._sync_business_key("alice", "dataqa", "mcp_new") == 2
    data = json.loads(target.read_text(encoding="utf-8"))
    for name in ("dataqa", "dataqa-2"):
        assert data["servers"][name]["headers"]["Authorization"] == "Bearer mcp_new"


def test_sync_returns_zero_without_matching_server(monkeypatch):
    target = _workdir() / "mcp.json"
    original = {"servers": {"akshare": {"url": "http://127.0.0.1:8005/sse"}}}
    target.write_text(json.dumps(original), encoding="utf-8")
    _patch_mcp_path(monkeypatch, target)
    _patch_side_effects(monkeypatch)

    assert settings_api._sync_business_key("alice", "dataqa", "mcp_new") == 0
    assert json.loads(target.read_text(encoding="utf-8")) == original


def test_sync_creates_nothing_when_user_has_no_mcp_file(monkeypatch):
    target = _workdir() / "alice" / "mcp.json"
    _patch_mcp_path(monkeypatch, target)
    _patch_side_effects(monkeypatch)

    assert settings_api._sync_business_key("alice", "dataqa", "mcp_new") == 0
    assert not target.exists()
