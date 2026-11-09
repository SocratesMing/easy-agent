"""DA-16：execute 不得继承宿主进程的完整环境变量。

``agent.sandbox_enabled=false`` 时命令直接以 backend 的 env 运行；改造前默认 backend
``inherit_env=True``，``.env`` 里的 API Key / JWT 密钥对 agent 的命令可见。
"""

from __future__ import annotations

import sys
import types

import pytest

from easy_agent.agent.backend import _build_backend_for_context
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.sandboxing.sandbox import host_safe_env

SECRET = "da16-super-secret-value"


def test_host_safe_env_keeps_shell_basics_and_drops_secrets():
    env = host_safe_env(
        {
            "PATH": "/usr/bin:/bin",
            "HOME": "/home/agent",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TMPDIR": "/tmp/x",
            "EASY_JWT_SECRET": SECRET,
            "DEEPSEEK_API_KEY": SECRET,
            "MYSQL_PASSWORD": SECRET,
        }
    )
    assert env == {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/agent",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMPDIR": "/tmp/x",
    }


def test_host_safe_env_does_not_pass_proxy_or_mirror_vars():
    """有意不放行代理 / 镜像 / 证书类变量（2026-10-19 决定）：代理 URL 可能带凭据。"""
    env = host_safe_env(
        {
            "PATH": "/usr/bin:/bin",
            "HTTP_PROXY": "http://user:pass@proxy:3128",
            "HTTPS_PROXY": "http://user:pass@proxy:3128",
            "https_proxy": "http://user:pass@proxy:3128",
            "NO_PROXY": "localhost",
            "PIP_INDEX_URL": "https://token@mirror.example/simple",
            "NPM_CONFIG_REGISTRY": "https://mirror.example/npm/",
            "SSL_CERT_FILE": "/etc/ssl/corp.pem",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/corp.pem",
        }
    )
    assert env == {"PATH": "/usr/bin:/bin"}


def _backend(tmp_path, *, sandbox_enabled: bool):
    ws = tmp_path / "workspace" / "alice" / "session" / "s1"
    skills = tmp_path / "workspace" / "alice" / "skills"
    ws.mkdir(parents=True)
    skills.mkdir(parents=True)
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(
            external_dirs={},
            workspace_dir=str(tmp_path / "workspace"),
            sandbox_enabled=sandbox_enabled,
        )
    )
    context = SessionContext(
        session_id="s1",
        username="alice",
        safe_username="alice",
        workspace_dir=str(ws),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(skills),
        memory_file=str(ws / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "alice" / "AGENTS.md"),
    )
    return _build_backend_for_context(config, context, ["/user-skills/"])


@pytest.mark.parametrize("sandbox_enabled", [True, False])
def test_default_backend_env_has_no_host_secrets(tmp_path, monkeypatch, sandbox_enabled):
    monkeypatch.delenv("EASY_SANDBOX", raising=False)
    monkeypatch.setenv("EASY_JWT_SECRET", SECRET)
    monkeypatch.setenv("DEEPSEEK_API_KEY", SECRET)

    backend = _backend(tmp_path, sandbox_enabled=sandbox_enabled)

    shell = backend.default.shell  # default 把 execute 转给会话 shell（DA-04）
    assert SECRET not in shell._env.values()
    assert shell._env.get("PATH")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX env 命令")
def test_unsandboxed_execute_does_not_see_host_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_SANDBOX", raising=False)
    monkeypatch.setenv("EASY_JWT_SECRET", SECRET)

    backend = _backend(tmp_path, sandbox_enabled=False)
    result = backend.execute("env")

    assert result.exit_code == 0
    assert "PATH=" in result.output
    assert SECRET not in result.output
