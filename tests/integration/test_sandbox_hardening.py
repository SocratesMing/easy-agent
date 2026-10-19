"""DA-07（C3）：沙箱网络开关、技能目录只读挂载、execute 超时上限。

改造前：bwrap 固定 ``--share-net``；技能目录在沙箱里整体可写，``skill_write=new-only`` /
``deny`` 只约束文件工具，``execute`` 照样能改写已有技能；模型给 execute 传多大的超时就用多大。
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

import easy_agent.backends.shell as shell_mod
import easy_agent.sandboxing.sandbox as sandbox_mod
from easy_agent.agent.backend import (
    _build_backend_for_context,
    _protected_skill_mounts,
    _sandbox_network,
)
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.backends.artifacts import AgentArtifactsBackend
from easy_agent.bootstrap.config import Config
from easy_agent.sandboxing.landlock import landlock_usable
from easy_agent.sandboxing.sandbox import bwrap_usable

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sandbox")


# ── 配置 ──────────────────────────────────────────────────────────────


def _agent_cfg(**agent):
    defaults = {"sandbox_network": "host", "skill_write": "new-only", "execute_max_timeout": 600}
    return types.SimpleNamespace(agent=types.SimpleNamespace(**{**defaults, **agent}))


def test_sandbox_network_defaults_to_host_and_rejects_unknown_values(caplog):
    assert _sandbox_network(_agent_cfg()) == "host"
    assert _sandbox_network(_agent_cfg(sandbox_network=" NONE ")) == "none"
    with caplog.at_level(logging.WARNING):
        assert _sandbox_network(_agent_cfg(sandbox_network="proxy")) == "host"
    assert "sandbox_network" in caplog.text


def test_config_parses_new_sandbox_keys(tmp_path):
    base = "model: m\nmodels:\n  m:\n    provider: test\n    api_key: sk-test\n"
    path = tmp_path / "config.yaml"
    path.write_text(base, encoding="utf-8")
    defaults = Config.from_yaml(path).agent
    assert (defaults.sandbox_network, defaults.execute_max_timeout) == ("host", 600)

    path.write_text(base + "sandbox_network: none\nexecute_max_timeout: 90\n", encoding="utf-8")
    custom = Config.from_yaml(path).agent
    assert (custom.sandbox_network, custom.execute_max_timeout) == ("none", 90)


# ── 技能目录只读挂载（策略 → 挂载参数） ──────────────────────────────────


@pytest.fixture()
def skills(tmp_path):
    root = tmp_path / "skills"
    (root / "docx").mkdir(parents=True)
    (root / "docx" / "SKILL.md").write_text("original", encoding="utf-8")
    (root / ".hidden").write_text("x", encoding="utf-8")
    return root


def test_new_only_protects_each_existing_entry(skills):
    binds, ro_dirs = _protected_skill_mounts(skills, "new-only")
    assert binds == [
        (str(skills / ".hidden"), "/user-skills/.hidden"),
        (str(skills / "docx"), "/user-skills/docx"),
    ]
    assert ro_dirs == [str(skills)]


def test_deny_protects_the_whole_root_and_allow_protects_nothing(skills):
    assert _protected_skill_mounts(skills, "deny") == ([(str(skills), "/user-skills")], [str(skills)])
    assert _protected_skill_mounts(skills, "allow") == ([], [])


# ── 沙箱参数 ──────────────────────────────────────────────────────────


class _Captured(Exception):
    pass


def _capture_bwrap_argv(monkeypatch, **kwargs) -> list[str]:
    def fake_run(argv, **_):
        raise _Captured(argv)

    monkeypatch.setattr(sandbox_mod.subprocess, "run", fake_run)
    with pytest.raises(_Captured) as captured:
        sandbox_mod.run_sandboxed(
            "true",
            binds=[("/real/skills", "/user-skills")],
            cwd="/workspace",
            env={},
            timeout=5,
            max_output_bytes=1000,
            **kwargs,
        )
    return captured.value.args[0]


def test_bwrap_network_flag_follows_the_setting(monkeypatch):
    assert "--share-net" in _capture_bwrap_argv(monkeypatch)
    assert "--share-net" not in _capture_bwrap_argv(monkeypatch, network="none")


def test_bwrap_read_only_overlays_come_after_the_writable_bind(monkeypatch):
    argv = _capture_bwrap_argv(
        monkeypatch, ro_binds=[("/real/skills/docx", "/user-skills/docx")]
    )
    rw = argv.index("--bind")
    ro = max(i for i, arg in enumerate(argv) if arg == "--ro-bind-try" and argv[i + 2] == "/user-skills/docx")
    assert rw < ro


def test_landlock_keeps_protected_dirs_out_of_the_writable_set(tmp_path, monkeypatch):
    monkeypatch.setattr(shell_mod, "bwrap_usable", lambda: False)
    monkeypatch.setattr(shell_mod, "landlock_usable", lambda: True)
    ws, skills_dir = tmp_path / "ws", tmp_path / "skills"
    ws.mkdir()
    skills_dir.mkdir()
    shell = shell_mod.PathTranslatingShell(
        path_mappings={"/workspace/": f"{ws}/", "/user-skills/": f"{skills_dir}/"},
        root_dir=str(ws),
        readonly_dirs=[str(skills_dir)],
    )
    assert str(ws.resolve()) in shell._allowed_rw_dirs
    assert str(skills_dir.resolve()) not in shell._allowed_rw_dirs


# ── execute 超时上限 ──────────────────────────────────────────────────


class _RecordingShell:
    id = "stub"

    def __init__(self):
        self.timeouts = []

    def execute(self, command, *, timeout=None):
        self.timeouts.append(timeout)


def test_model_requested_timeout_is_capped(tmp_path):
    shell = _RecordingShell()
    backend = AgentArtifactsBackend(tmp_path, shell, max_timeout=600)
    for requested in (3600, 30, None):
        backend.execute("sleep 1", timeout=requested)
    assert shell.timeouts == [600, 30, None]


# ── 端到端：真实 bwrap / Landlock ─────────────────────────────────────


def _session(tmp_path, skills_dir, **agent):
    ws = tmp_path / "workspace" / "alice" / "session" / "s1"
    ws.mkdir(parents=True)
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(
            external_dirs={},
            workspace_dir=str(tmp_path / "workspace"),
            sandbox_enabled=True,
            **{"sandbox_network": "host", "skill_write": "new-only", **agent},
        )
    )
    ctx = SessionContext(
        session_id="s1",
        username="alice",
        safe_username="alice",
        workspace_dir=str(ws),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(skills_dir),
        memory_file=str(ws / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "alice" / "AGENTS.md"),
    )
    return _build_backend_for_context(config, ctx, ["/user-skills/"])


bwrap_only = pytest.mark.skipif(not bwrap_usable(), reason="bwrap not available")


@POSIX
@bwrap_only
def test_network_none_leaves_only_loopback(tmp_path, skills):
    backend = _session(tmp_path, skills, sandbox_network="none")
    result = backend.execute("cat /proc/net/dev")
    interfaces = {line.split(":")[0].strip() for line in result.output.splitlines() if ":" in line}
    assert result.exit_code == 0
    assert interfaces == {"lo"}


@POSIX
@bwrap_only
def test_new_only_skills_are_read_only_inside_bwrap(tmp_path, skills):
    backend = _session(tmp_path, skills)
    edit = backend.execute("echo changed > /user-skills/docx/SKILL.md")
    assert edit.exit_code != 0
    assert (skills / "docx" / "SKILL.md").read_text(encoding="utf-8") == "original"

    create = backend.execute("mkdir /user-skills/fresh && echo new > /user-skills/fresh/SKILL.md")
    assert create.exit_code == 0
    assert (skills / "fresh" / "SKILL.md").read_text(encoding="utf-8").strip() == "new"


@POSIX
@bwrap_only
def test_deny_and_allow_policies_inside_bwrap(tmp_path, skills):
    denied = _session(tmp_path / "deny", skills, skill_write="deny")
    assert denied.execute("mkdir /user-skills/fresh").exit_code != 0

    allowed = _session(tmp_path / "allow", skills, skill_write="allow")
    assert allowed.execute("echo changed > /user-skills/docx/SKILL.md").exit_code == 0
    assert (skills / "docx" / "SKILL.md").read_text(encoding="utf-8").strip() == "changed"


@POSIX
@pytest.mark.skipif(not landlock_usable(), reason="Landlock not available")
def test_new_only_skills_are_read_only_under_landlock(tmp_path, skills, monkeypatch):
    monkeypatch.setattr(shell_mod, "bwrap_usable", lambda: False)
    backend = _session(tmp_path, skills)
    ws = Path(tmp_path / "workspace" / "alice" / "session" / "s1")

    assert backend.execute(f"echo changed > {skills}/docx/SKILL.md").exit_code != 0
    assert (skills / "docx" / "SKILL.md").read_text(encoding="utf-8") == "original"
    assert backend.execute("echo ok > /workspace/out.txt").exit_code == 0
    assert (ws / "out.txt").read_text(encoding="utf-8").strip() == "ok"
