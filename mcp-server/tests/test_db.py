"""MySQL 连接配置读取。

`describe_target()` 是启动日志用的：库名与主应用配错时的症状是"签了 key 却
一律 401"，没有任何报错指向配置，所以这两点必须钉住 ——
① 描述里能看到真实的 host/port/database；② 配置非法时不抛异常。
"""

from __future__ import annotations

import pytest

from easy_mcp_server import db as db_mod


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch):
    """本模块一律不加载 mcp-server/.env。

    ``env.load_env()`` 只把 .env 解析一遍就直接写 ``os.environ``（`_LOADED`
    幂等），而本模块的用例会 setenv / 替换 environ。若这里触发了真实加载，
    monkeypatch 回滚时会把 .env 注入的值一并删掉且不会重新注入，后续依赖
    真实库的用例（如 strategyqa）就会静默掉到缺省值 ``MYSQL_DATABASE=agent``
    而失败 —— 症状离病因很远，所以直接断掉。
    """
    monkeypatch.setattr(db_mod, "load_env", lambda *a, **k: [])


def test_describe_target_shows_host_port_database(monkeypatch):
    monkeypatch.setenv("MYSQL_HOST", "10.0.0.7")
    monkeypatch.setenv("MYSQL_PORT", "3307")
    monkeypatch.setenv("MYSQL_DATABASE", "market")

    assert db_mod.describe_target() == "10.0.0.7:3307/market"


def test_describe_target_never_leaks_password(monkeypatch):
    monkeypatch.setenv("MYSQL_PASSWORD", "super-secret")

    assert "super-secret" not in db_mod.describe_target()


def test_describe_target_tolerates_invalid_port(monkeypatch):
    """诊断信息不该反过来成为启动失败的原因。"""
    monkeypatch.setenv("MYSQL_PORT", "not-a-port")

    described = db_mod.describe_target()

    assert described.startswith("<配置无效")
    assert "not-a-port" in described or "invalid literal" in described


def test_mysql_config_uses_defaults_when_env_missing(monkeypatch):
    """环境变量缺失时的缺省值。

    刻意用「整体替换 environ」而不是逐个 ``delenv``：后者会连累后续用例
    （见上面的 ``_no_env_file``）。
    """
    monkeypatch.setattr(db_mod.os, "environ", {})

    config = db_mod.mysql_config()

    assert config["host"] == "127.0.0.1"
    assert config["port"] == 3306
    assert config["database"] == "agent"
    assert config["charset"] == "utf8mb4"
