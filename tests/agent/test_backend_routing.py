"""DA-04 / DA-05：会话 backend 的路由。

DA-04：改造前 ``CompositeBackend.default`` 的根目录就是会话工作区，``/secret/a.txt`` 与
``/workspace/secret/a.txt`` 是同一个文件（写在 ``/workspace/...`` 上的 deny 规则可被绕过），
框架产物 ``/large_tool_results/``、``/conversation_history/`` 也写进了用户工作区。

DA-05：改造前 ``ContextBackend`` 靠 ``CompositeBackend.register`` 冒充子类、返回占位
default / routes，框架的 symlink 感知权限校验（``_resolve_real_path``）因此静默失效。
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.middleware.filesystem import (
    FilesystemPermission,
    _permission_error,
    _route_host_path_prompt,
    _uses_state_backend,
)

from easy_agent.agent.backend import _build_backend_for_context, agent_artifacts_dir
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.api.files import build_workspace_tree
from easy_agent.backends.context import ContextBackend, SessionCompositeBackend

ROUTES = ["/workspace/", "/memories/", "/user-skills/"]
DENY_SECRET = [FilesystemPermission(operations=["read", "write"], paths=["/workspace/secret/**"], mode="deny")]


@pytest.fixture()
def env(tmp_path):
    ws = tmp_path / "workspace" / "alice" / "session" / "s1"
    skills = tmp_path / "workspace" / "alice" / "skills"
    ws.mkdir(parents=True)
    skills.mkdir(parents=True)
    (ws / "secret").mkdir()
    (ws / "secret" / "a.txt").write_text("TOP-SECRET", encoding="utf-8")
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(
            external_dirs={}, workspace_dir=str(tmp_path / "workspace"), sandbox_enabled=False
        )
    )
    ctx = SessionContext(
        session_id="s1",
        username="alice",
        safe_username="alice",
        workspace_dir=str(ws),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(skills),
        memory_file=str(ws / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "alice" / "AGENTS.md"),
    )
    return types.SimpleNamespace(tmp=tmp_path, ws=ws, config=config, ctx=ctx)


def _session_backend(env):
    return _build_backend_for_context(env.config, env.ctx, ["/user-skills/"])


def _graph_backend(env):
    """交给 create_deep_agent 的顶层 backend（非共享图用 fallback context 解析）。"""
    return ContextBackend(
        lambda ctx: _build_backend_for_context(env.config, ctx, ["/user-skills/"]),
        fallback_context=env.ctx,
        route_prefixes=ROUTES,
    )


# ── DA-04：default 不再是工作区的别名 ──────────────────────────────────


def test_root_paths_no_longer_alias_the_workspace(env):
    backend = _session_backend(env)
    assert backend.read("/workspace/secret/a.txt").file_data["content"] == "TOP-SECRET"
    aliased = backend.read("/secret/a.txt")
    assert aliased.error and "TOP-SECRET" not in str(aliased.file_data)


def test_framework_artifacts_live_outside_the_workspace(env):
    backend = _graph_backend(env)
    assert backend.artifacts_root == "/.agent"
    for path in ("/.agent/large_tool_results/call_abc", "/.agent/conversation_history/s1.md"):
        assert backend.write(path, "artifact").error is None

    agent_dir = agent_artifacts_dir("alice", "s1", env.config)
    assert (agent_dir / ".agent" / "large_tool_results" / "call_abc").is_file()
    assert not (env.ws / ".agent").exists()
    assert [p.name for p in env.ws.rglob("*") if p.is_file()] == ["a.txt"]


def test_writes_outside_any_mount_are_rejected_with_a_hint(env):
    backend = _graph_backend(env)
    result = backend.write("/report.md", "lost")
    assert "/workspace/report.md" in result.error
    assert not (agent_artifacts_dir("alice", "s1", env.config) / "report.md").exists()
    assert backend.write("/workspace/report.md", "kept").error is None
    assert (env.ws / "report.md").read_text(encoding="utf-8") == "kept"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell")
def test_execute_still_runs_in_the_session_workspace(env):
    backend = _graph_backend(env)
    result = backend.execute("echo hi > out.txt && ls")
    assert result.exit_code == 0
    assert (env.ws / "out.txt").read_text(encoding="utf-8").strip() == "hi"


def test_user_workspace_tree_hides_the_artifacts_dir(env):
    agent_artifacts_dir("alice", "s1", env.config).mkdir(parents=True)
    names = [item["name"] for item in build_workspace_tree(env.tmp / "workspace" / "alice")]
    assert ".agent" not in names
    assert "session" in names


# ── DA-05：两层都是真正的 CompositeBackend ─────────────────────────────


def test_context_backend_layers_are_real_composites(env):
    backend = _graph_backend(env)
    assert CompositeBackend in type(backend).__mro__
    assert isinstance(backend.default, SessionCompositeBackend)
    assert CompositeBackend in type(backend.default).__mro__
    # 会话层解析出的是本会话真实的路由叶子
    assert set(backend.default.routes) == set(ROUTES)


def test_symlink_alias_inside_a_route_is_denied(env):
    """``/workspace/innocent`` → ``secret``：框架顺着 symlink 判定到 deny 规则（改造前放行）。"""
    (env.ws / "innocent").symlink_to("secret", target_is_directory=True)
    backend = _graph_backend(env)

    error = _permission_error(DENY_SECRET, "read", backend, "/workspace/innocent/a.txt")
    assert error and "/workspace/secret/a.txt" in error
    assert _permission_error(DENY_SECRET, "read", backend, "/workspace/secret/a.txt")


def test_no_route_to_host_path_prompt_is_generated(env):
    """沙箱里命令按虚拟路径访问挂载目录；框架不能生成「改用宿主机路径」的提示。"""
    assert _route_host_path_prompt(_graph_backend(env)) == ""


def test_build_time_introspection_needs_no_context():
    backend = ContextBackend(lambda ctx: pytest.fail("建图期不应构建会话 backend"), route_prefixes=ROUTES)
    assert _uses_state_backend(backend) is False


def test_calls_without_a_context_fail_clearly():
    backend = ContextBackend(lambda ctx: pytest.fail("不应构建"), route_prefixes=ROUTES)
    with pytest.raises(RuntimeError, match="SessionContext"):
        backend.read("/workspace/a.txt")
    with pytest.raises(RuntimeError, match="SessionContext"):
        backend.execute("ls")


def test_session_artifacts_dir_key_is_sanitized(tmp_path):
    config = types.SimpleNamespace(agent=types.SimpleNamespace(workspace_dir=str(tmp_path)))
    path = agent_artifacts_dir("alice", "../../etc", config)
    assert path.parent == Path(tmp_path) / "alice" / ".agent"
    assert ".." not in path.name
