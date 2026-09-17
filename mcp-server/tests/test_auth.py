"""鉴权测试：缺失/无效 key、身份注入、跨业务隔离、重签与缓存行为。"""

from __future__ import annotations

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import easy_mcp_server.keys as keys_module
from easy_mcp_server.keys import create_mysql_verifier, hash_api_key

from support import FakeKeyStore, build_hello


async def _call(server: str, tool: str, args: dict, api_key: str | None):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with streamablehttp_client(f"{server}/mcp/hello/", headers=headers) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            return await session.call_tool(tool, args)


async def test_missing_api_key_is_rejected(server):
    with pytest.raises(Exception):
        await _call(server, "echo", {"text": "x"}, None)


async def test_invalid_api_key_is_rejected(server):
    with pytest.raises(Exception):
        await _call(server, "echo", {"text": "x"}, "key-nope")


async def test_revoked_key_is_rejected(server, key_store):
    key_store.revoke("hello", "key-bob")
    with pytest.raises(Exception):
        await _call(server, "echo", {"text": "x"}, "key-bob")


async def test_tool_sees_username_from_api_key(server):
    result = await _call(server, "whoami", {}, "key-alice")
    payload = result.content[0].text
    assert "alice" in payload
    assert "hello" in payload


async def test_different_keys_resolve_different_users(server):
    result = await _call(server, "whoami", {}, "key-bob")
    assert "bob" in result.content[0].text


async def test_key_is_bound_to_business(server, key_store):
    """同一用户的 key 不能跨业务使用（每用户每业务一把）。"""
    import httpx

    key_store.issue("second", "key-for-second", "alice")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{server}/mcp/hello/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer key-for-second"},
        )
    assert resp.status_code == 401


# ── MySQL 校验器（mock 数据库） ─────────────────────────────────────────


@pytest.fixture
def fake_db(monkeypatch):
    """把 keys.py 的数据库查询替换为内存表（并清空全局缓存避免测试间泄漏）。"""
    keys_module.clear_cache()
    table: dict[tuple[str, str], str] = {}  # (business, key_hash) -> username

    def fake_fetch_one(sql, params=()):
        business, key_hash = params
        username = table.get((business, key_hash))
        return {"username": username} if username else None

    monkeypatch.setattr(keys_module, "fetch_one", fake_fetch_one)
    return table


def test_hash_is_sha256_hex():
    assert hash_api_key("abc") == hash_api_key("abc")
    assert len(hash_api_key("abc")) == 64
    assert hash_api_key("abc") != hash_api_key("abd")


def test_verifier_resolves_username(fake_db):
    fake_db[("market", hash_api_key("k1"))] = "alice"
    verify = create_mysql_verifier()
    assert verify("market", "k1") == "alice"
    assert verify("market", "bad") is None


def test_verifier_caches_within_ttl(fake_db, monkeypatch):
    fake_db[("market", hash_api_key("k1"))] = "alice"
    verify = create_mysql_verifier(ttl_seconds=60)
    assert verify("market", "k1") == "alice"

    # 重签：数据库里换成 bob —— 缓存未过期时仍返回旧用户（预期内的吊销延迟）
    fake_db[("market", hash_api_key("k1"))] = "bob"
    assert verify("market", "k1") == "alice"

    # 缓存过期后读到新值
    monkeypatch.setattr(keys_module.time, "monotonic", lambda: 1e12)
    assert verify("market", "k1") == "bob"


def test_verifier_rejects_all_when_db_fails(monkeypatch):
    keys_module.clear_cache()

    def broken_fetch_one(sql, params=()):
        raise RuntimeError("db down")

    monkeypatch.setattr(keys_module, "fetch_one", broken_fetch_one)
    verify = create_mysql_verifier()
    assert verify("market", "k1") is None
