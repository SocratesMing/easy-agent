"""querykit 共享底座测试：SQL 只读网关、标识符校验、探索策略（不连数据库）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from easy_mcp_server.querykit import (
    ExplorePolicy,
    assert_identifier,
    assert_readonly,
    json_safe,
    max_rows,
    policy_from_env,
)


# ── SQL 只读网关 ────────────────────────────────────────────────────────


def test_select_allowed_and_limit_appended(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "100")
    sql = assert_readonly("SELECT STRATEGY_ID FROM fmut2_strategy_manage")
    assert sql.endswith("LIMIT 100")


def test_with_cte_allowed():
    assert assert_readonly("WITH t AS (SELECT 1 AS n) SELECT n FROM t").startswith("WITH")


def test_small_limit_preserved(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "500")
    assert assert_readonly("SELECT * FROM t LIMIT 10").endswith("LIMIT 10")


def test_large_limit_capped(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "50")
    assert assert_readonly("SELECT * FROM t LIMIT 99999").endswith("LIMIT 50")


def test_offset_form_left_untouched(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "10")
    assert assert_readonly("SELECT * FROM t LIMIT 100, 200").endswith("LIMIT 100, 200")


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a=1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "CREATE TABLE t (id INT)",
        "TRUNCATE TABLE t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT SLEEP(10)",
        "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    ],
)
def test_write_and_dangerous_statements_rejected(sql):
    with pytest.raises(ValueError):
        assert_readonly(sql)


def test_stacked_statements_rejected():
    with pytest.raises(ValueError):
        assert_readonly("SELECT 1; DROP TABLE t")


def test_write_hidden_after_line_comment_rejected():
    """先剥注释再判断：注释内容无害，但注释后的堆叠语句必须拦下。"""
    with pytest.raises(ValueError):
        assert_readonly("SELECT 1 -- 查询\n; DROP TABLE t")


def test_system_schema_rejected():
    with pytest.raises(ValueError):
        assert_readonly("SELECT * FROM information_schema.TABLES")


def test_server_variables_rejected():
    with pytest.raises(ValueError):
        assert_readonly("SELECT @@version")


def test_empty_sql_rejected():
    with pytest.raises(ValueError):
        assert_readonly("   ")


def test_strategy_style_query_passes_gateway():
    """业务列名（AUTH_VIEW / UPDATE_TIME 等）不能被关键字检查误伤。"""
    sql = assert_readonly(
        "SELECT STRATEGY_ID, AUTHOR FROM fmut2_strategy_manage "
        "WHERE AUTH_VIEW = 1 ORDER BY UPDATE_TIME DESC"
    )
    assert "LIMIT" in sql


# ── 标识符与序列化 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["t;drop", "1abc", "a b", "", "t`x", "t.x", "表"])
def test_invalid_identifier_rejected(name):
    with pytest.raises(ValueError):
        assert_identifier(name)


def test_valid_identifier_accepts_backticks():
    assert assert_identifier("`fmut2_strategy_manage`") == "fmut2_strategy_manage"


def test_json_safe_converts_decimal_and_date():
    assert json_safe({"y": Decimal("0.155"), "d": date(2026, 9, 21)}) == {
        "y": 0.155,
        "d": "2026-09-21",
    }


def test_max_rows_default(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "")
    assert max_rows() == 500


# ── 表级可见性策略 ──────────────────────────────────────────────────────


def test_default_policy_denies_api_keys_and_internal_tables():
    policy = ExplorePolicy()
    assert policy.is_visible("fmut2_strategy_manage") is True
    assert policy.is_visible("mcp_api_keys") is False
    assert policy.is_visible("_internal") is False


def test_allowlist_restricts_visibility():
    policy = ExplorePolicy(allowed=("fmut2_strategy_manage",))
    assert policy.is_visible("fmut2_strategy_manage") is True
    assert policy.is_visible("users") is False


def test_denylist_wins_over_allowlist():
    policy = ExplorePolicy(allowed=("t1", "t2"), denied=("t2",))
    assert policy.is_visible("t1") is True
    assert policy.is_visible("t2") is False


def test_policy_from_env_reads_business_prefix(monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWED_TABLES", "fmut2_strategy_manage")
    monkeypatch.setenv("STRATEGY_DENY_TABLES", "users")
    policy = policy_from_env("STRATEGY_")
    assert policy.allowed == ("fmut2_strategy_manage",)
    assert "users" in policy.denied
    # 默认敏感表仍然被屏蔽
    assert "mcp_api_keys" in policy.denied
    assert policy.is_visible("users") is False


def test_policy_from_env_empty_means_unrestricted(monkeypatch):
    monkeypatch.setenv("STRATEGY_ALLOWED_TABLES", "")
    assert policy_from_env("STRATEGY_").allowed is None
