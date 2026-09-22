"""对外契约测试：这是主应用唯一被允许依赖的东西。

契约一旦漂移，主应用侧的"业务识别 / Key 签发"就会静默失效（表现为 401），
所以这里把清单、URL 形态、哈希算法、表结构都钉住。
"""

from __future__ import annotations

import hashlib
import subprocess
import sys

from easy_mcp_server import contract, registry


# ── 业务清单 ───────────────────────────────────────────────────────────


def test_business_names_are_discovered_from_packages():
    names = contract.business_names()

    assert names == sorted(names), "清单需稳定有序，主应用下拉框依赖它"
    assert "strategyqa" in names


def test_business_names_do_not_import_business_packages():
    """只扫描目录：清单在注册表发布、管理脚本里用，不该拉起 FastMCP/连接池。

    必须在新进程里验证——同进程跑整个测试套件时，业务包早被别的用例 import 过了。
    """
    code = (
        "import sys\n"
        "from easy_mcp_server import contract\n"
        "assert contract.business_names()\n"
        "left = sorted(m for m in sys.modules if m.startswith('easy_mcp_server.businesses.'))\n"
        "print(','.join(left))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", f"清单发现不应 import 业务包: {proc.stdout.strip()}"


# ── URL 形态 ───────────────────────────────────────────────────────────


def test_business_url_has_trailing_slash():
    """尾斜杠不能省：MCP 客户端不跟随 307（/mcp/x 会 307 到 /mcp/x/）。"""
    assert (
        contract.business_url("http://127.0.0.1:8100", "market")
        == "http://127.0.0.1:8100/mcp/market/"
    )


def test_business_url_normalizes_base_trailing_slash():
    assert (
        contract.business_url("http://mcp.example.com/", "strategyqa")
        == "http://mcp.example.com/mcp/strategyqa/"
    )


def test_business_from_url_matches_contract_and_main_app_rule():
    base = "http://127.0.0.1:8100"
    for name in contract.business_names():
        assert contract.business_from_url(contract.business_url(base, name)) == name

    # 主应用设置页用的是同一形态（含无尾斜杠写法）
    assert contract.business_from_url(f"{base}/mcp/market") == "market"
    assert contract.business_from_url("http://127.0.0.1:8005/sse") is None


# ── 鉴权 ───────────────────────────────────────────────────────────────


def test_hash_api_key_matches_main_app_algorithm():
    """主应用按 sha256 十六进制落库，本模块必须用同一算法反查。"""
    assert contract.hash_api_key("mcp_abc") == hashlib.sha256(b"mcp_abc").hexdigest()


def test_api_key_prefix_is_shared_constant():
    from easy_mcp_server.keys import hash_api_key as exported_hash

    assert contract.API_KEY_PREFIX == "mcp_"
    # keys.py 不再自带第二份实现，直接复用契约
    assert exported_hash("mcp_abc") == contract.hash_api_key("mcp_abc")


# ── 数据契约 ───────────────────────────────────────────────────────────


def test_api_keys_table_sql_matches_declared_table():
    sql = contract.api_keys_table_sql()

    assert f"CREATE TABLE IF NOT EXISTS {contract.API_KEY_TABLE}" in sql
    # 主应用侧签发逻辑依赖的列
    for column in ("username", "business", "key_hash", "revoked"):
        assert column in sql


def test_registry_table_sql_declares_name_pk():
    sql = contract.business_registry_table_sql()

    assert f"CREATE TABLE IF NOT EXISTS {contract.BUSINESS_REGISTRY_TABLE}" in sql
    assert "name VARCHAR(64) PRIMARY KEY" in sql


# ── 注册表发布 ─────────────────────────────────────────────────────────


def test_publish_is_noop_for_empty_list():
    assert registry.publish([]) == 0


def test_publish_swallows_db_failure(monkeypatch):
    """注册表是"发现通道"不是运行必需：写库失败只能告警，不能拖垮启动。"""

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(registry, "connection", boom)

    assert registry.publish(["market"]) == 0


def test_publish_upserts_every_business(monkeypatch):
    executed: list[tuple[str, tuple | None]] = []

    class _Cursor:
        def execute(self, sql, params=None):
            executed.append((sql, params))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(registry, "connection", lambda: _Conn())

    assert registry.publish(["market", "strategyqa"]) == 2

    # 第一次是自愈建表，之后每个业务一条 upsert
    assert len(executed) == 3
    assert executed[0][0] == contract.business_registry_table_sql()
    assert [params[0] for _, params in executed[1:]] == ["market", "strategyqa"]
