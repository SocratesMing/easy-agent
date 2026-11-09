"""DA-13 S2：公共技能在 execute 沙箱里只读（真实 bwrap / Landlock）。

- bwrap：只把已启用的公共技能逐个只读挂到 ``/skills/builtin/<name>``，其余公共技能和
  canonical 目录的真实路径都不可见；
- Landlock：不能按子目录放行，整个公共技能目录只读可见（公共技能本来对所有用户可见，
  技能中心可浏览）；命令里的 ``/skills/builtin/`` 翻译成真实路径，输出再翻译回来。

两种模式下都不能改写、删除公共技能。
"""

from __future__ import annotations

import types

import pytest

import easy_agent.backends.shell as sandbox_shell_mod
from easy_agent.agent.backend import _build_backend_for_context
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.sandboxing.landlock import landlock_usable
from easy_agent.sandboxing.sandbox import bwrap_usable


def _skill(root, name: str, marker: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name}\n---\n{marker}-BODY\n", encoding="utf-8"
    )
    (skill_dir / "run.sh").write_text(f"echo RAN-{marker}\n", encoding="utf-8")


def _backend(tmp_path, monkeypatch, *, bwrap: bool):
    monkeypatch.setattr(sandbox_shell_mod, "bwrap_usable", lambda: bwrap)
    monkeypatch.setattr(sandbox_shell_mod, "landlock_usable", lambda: not bwrap)

    canonical = tmp_path / "skills"
    _skill(canonical, "demo", "DEMO")
    _skill(canonical, "hidden", "HIDDEN")
    ws = tmp_path / "workspace" / "alice" / "session" / "s1"
    ws.mkdir(parents=True)
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(
            external_dirs={}, workspace_dir=str(tmp_path / "workspace"), sandbox_enabled=True
        )
    )
    ctx = SessionContext(
        session_id="s1",
        username="alice",
        safe_username="alice",
        workspace_dir=str(ws),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(tmp_path / "workspace" / "alice" / "skills"),
        memory_file=str(ws / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "alice" / "AGENTS.md"),
        builtin_skills_dir=str(canonical),
        builtin_skills=("demo",),
    )
    backend = _build_backend_for_context(config, ctx, ["/skills/builtin/", "/user-skills/"])
    return backend, canonical


def _assert_read_only(backend, canonical) -> None:
    run = backend.execute("sh /skills/builtin/demo/run.sh")
    assert run.exit_code == 0, run.output
    assert "RAN-DEMO" in run.output
    assert "DEMO-BODY" in backend.execute("cat /skills/builtin/demo/SKILL.md").output

    backend.execute("echo x > /skills/builtin/demo/new.txt")
    backend.execute("rm -f /skills/builtin/demo/SKILL.md")
    backend.execute("echo hacked >> /skills/builtin/demo/run.sh")
    assert not (canonical / "demo" / "new.txt").exists()
    assert (canonical / "demo" / "SKILL.md").is_file()
    assert (canonical / "demo" / "run.sh").read_text(encoding="utf-8") == "echo RAN-DEMO\n"

    # 工作区照常可写
    assert backend.execute("echo ok > /workspace/out.txt && cat /workspace/out.txt").exit_code == 0


@pytest.mark.skipif(not bwrap_usable(), reason="bwrap 不可用")
def test_bwrap_mounts_only_enabled_builtin_skills_read_only(tmp_path, monkeypatch):
    backend, canonical = _backend(tmp_path, monkeypatch, bwrap=True)
    assert backend.default.shell._use_bwrap is True

    _assert_read_only(backend, canonical)

    listing = backend.execute("ls /skills/builtin/")
    assert "demo" in listing.output and "hidden" not in listing.output
    assert "HIDDEN-BODY" not in backend.execute("cat /skills/builtin/hidden/SKILL.md").output
    assert "HIDDEN-BODY" not in backend.execute(f"cat {canonical}/hidden/SKILL.md").output


@pytest.mark.skipif(not landlock_usable(), reason="Landlock 不可用")
def test_landlock_keeps_builtin_skills_read_only(tmp_path, monkeypatch):
    backend, canonical = _backend(tmp_path, monkeypatch, bwrap=False)
    shell = backend.default.shell
    assert shell._use_bwrap is False and shell._use_landlock is True

    _assert_read_only(backend, canonical)

    # 输出里的真实路径翻译回虚拟路径，模型看不到宿主机目录
    pwd = backend.execute("cd /skills/builtin/demo && pwd")
    assert pwd.output.strip() == "/skills/builtin/demo"
    assert str(canonical) not in pwd.output
