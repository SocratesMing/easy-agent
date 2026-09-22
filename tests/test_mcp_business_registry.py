"""MCP 业务清单来源测试：清单归 mcp-server 所有，主应用只读 + 兜底。

只测纯逻辑，不连数据库：`get_database()` 被替换为假实现，
验证「读得到就用登记的、读不到/为空就用内置兜底」两条路径。
"""

from __future__ import annotations

import easy_agent.services.mcp_api_keys as keys_service

REAL = keys_service.supported_businesses


class _FakeDB:
    """只实现本次用到的一个方法。"""

    def __init__(self, names: list[str] | None = None, error: Exception | None = None):
        self._names = names or []
        self._error = error

    def list_mcp_businesses(self) -> list[str]:
        if self._error:
            raise self._error
        return list(self._names)


def _patch_db(monkeypatch, fake: _FakeDB) -> None:
    monkeypatch.setattr(keys_service, "get_database", lambda: fake)
    keys_service.invalidate_business_cache()


# ── 以 mcp-server 登记为准 ──────────────────────────────────────────────


def test_uses_registered_businesses(monkeypatch):
    """新增业务只改 mcp-server：登记表里有就能签发，主应用无需改代码。"""
    _patch_db(monkeypatch, _FakeDB(["gamma", "market", "strategyqa"]))

    assert REAL() == ("gamma", "market", "strategyqa")
    assert keys_service.is_supported_business("gamma") is True


def test_registered_list_overrides_builtin(monkeypatch):
    """登记表是权威来源，可以出现内置兜底里根本没有的业务。"""
    _patch_db(monkeypatch, _FakeDB(["brand-new"]))

    assert REAL() == ("brand-new",)
    assert keys_service.is_supported_business("market") is False


# ── 兜底路径（mcp-server 未部署/表为空） ────────────────────────────────


def test_falls_back_when_registry_empty(monkeypatch):
    """表存在但为空（mcp-server 从未启动）→ 回退内置清单，设置页仍可用。"""
    _patch_db(monkeypatch, _FakeDB([]))

    assert REAL() == keys_service.SUPPORTED_BUSINESSES


def test_falls_back_when_registry_unavailable(monkeypatch):
    """共享库读失败不能把整条链路拖垮，只降级为内置清单。"""
    _patch_db(monkeypatch, _FakeDB(error=RuntimeError("db down")))

    assert REAL() == keys_service.SUPPORTED_BUSINESSES
    assert keys_service.is_supported_business("strategyqa") is True


# ── 缓存 ────────────────────────────────────────────────────────────────


def test_result_is_cached(monkeypatch):
    """清单读取有 60s 缓存：展示/签发是低频动作，但调用点可能在循环里。"""
    calls: list[int] = []

    class _CountingDB(_FakeDB):
        def list_mcp_businesses(self):
            calls.append(1)
            return super().list_mcp_businesses()

    _patch_db(monkeypatch, _CountingDB(["market"]))

    REAL()
    REAL()
    REAL()

    assert len(calls) == 1


def test_cache_can_be_invalidated(monkeypatch):
    _patch_db(monkeypatch, _FakeDB(["market"]))
    assert REAL() == ("market",)

    _patch_db(monkeypatch, _FakeDB(["strategyqa"]))
    keys_service.invalidate_business_cache()

    assert REAL() == ("strategyqa",)
