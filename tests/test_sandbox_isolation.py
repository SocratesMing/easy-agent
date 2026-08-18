"""Shell 沙箱跨用户隔离测试。

验证 Agent 的 shell ``execute`` 工具无法越权访问 ``/workspace`` 下其他用户的
会话记录。重点覆盖 bwrap 不可用时的降级路径：

- Landlock 可用：命令被限制在当前用户工作区，``cd ../..`` 等相对穿越、读取其他
  用户真实路径、``find`` 搜索其他用户文件均被拒绝。
- bwrap 与 Landlock 均不可用：fail-closed，拒绝执行（exit 127）。
"""

import pytest
from types import SimpleNamespace

import easy_agent.agent as agent_mod
import easy_agent.landlock as landlock_mod
import easy_agent.sandbox as sandbox_mod
from deepagents.backends import LocalShellBackend
from easy_agent.agent import _PathTranslatingShell
from deepagents.backends.protocol import ExecuteResponse


def _make_shell(tmp_path, monkeypatch, *, bwrap=False, landlock=True):
    """构造一个 _PathTranslatingShell，强制指定沙箱模式。

    userA 的工作区为 allowed；userB 的工作区为同级「其他用户」目录（不应可访问）。
    """
    monkeypatch.setattr(agent_mod, "bwrap_usable", lambda: bwrap)
    monkeypatch.setattr(agent_mod, "landlock_usable", lambda: landlock)

    workspace_root = tmp_path / "workspace"
    user_a = workspace_root / "userA" / "session" / "s1"
    user_b = workspace_root / "userB" / "session" / "s2"
    user_a.mkdir(parents=True)
    user_b.mkdir(parents=True)
    (user_a / "my.txt").write_text("A-data", encoding="utf-8")
    (user_b / "secret.md").write_text("B-SECRET-LEAK", encoding="utf-8")

    shell = _PathTranslatingShell(
        path_mappings={"/workspace/userA/session/s1/": f"{user_a}/"},
        root_dir=str(user_a),
        virtual_mode=True,
        inherit_env=True,
        timeout=30,
        sandbox_enabled=True,
    )
    return shell, user_a, user_b


@pytest.mark.skipif(
    not landlock_mod.landlock_usable(),
    reason="Landlock 不可用，无法测试 Landlock 降级路径",
)
def test_landlock_blocks_cross_user_access(tmp_path, monkeypatch):
    shell, user_a, user_b = _make_shell(tmp_path, monkeypatch, bwrap=False, landlock=True)
    assert shell._use_landlock is True

    # 自己工作区内的操作正常
    own = shell.execute("cat /workspace/userA/session/s1/my.txt")
    assert own.exit_code == 0
    assert "A-data" in own.output

    # 相对路径穿越到 workspace 根（原漏洞：列出所有用户）→ 被拒绝
    traverse = shell.execute(
        "cd /workspace/userA/session/s1/ && cd ../../../../ && ls"
    )
    assert "B-SECRET-LEAK" not in traverse.output
    assert traverse.exit_code != 0 or "Permission denied" in traverse.output

    # 直接读取其他用户真实路径 → 被拒绝
    cross = shell.execute(f"cat {user_b / 'secret.md'}")
    assert "B-SECRET-LEAK" not in cross.output
    assert cross.exit_code != 0

    # find 搜索其他用户的 memory.md → 无结果
    search = shell.execute(
        "cd /workspace/userA/session/s1/ && find ../../.. -name '*.md' 2>/dev/null"
    )
    assert "B-SECRET-LEAK" not in search.output


def test_fail_closed_when_no_isolation(tmp_path, monkeypatch):
    shell, _user_a, _user_b = _make_shell(
        tmp_path, monkeypatch, bwrap=False, landlock=False
    )
    assert shell._use_bwrap is False
    assert shell._use_landlock is False

    result = shell.execute("cat /workspace/userA/session/s1/my.txt")
    # fail-closed：命令被拒绝，不执行
    assert result.exit_code == 127
    assert "拒绝" in result.output or "Landlock" in result.output


def test_windows_virtual_paths_translate_between_posix_and_windows(tmp_path, monkeypatch):
    real_workspace = r"C:\Users\Agent\workspace"
    shell = _PathTranslatingShell(
        path_mappings={"/workspace/": real_workspace + "\\"},
        root_dir=tmp_path,
        virtual_mode=True,
        sandbox_enabled=False,
    )
    shell._is_windows = True
    captured = {}

    def fake_execute(self, command, *, timeout=None):
        captured["command"] = command
        return ExecuteResponse(
            output="C:/Users/Agent/workspace/result.txt",
            exit_code=0,
            truncated=False,
        )

    monkeypatch.setattr(agent_mod.LocalShellBackend, "execute", fake_execute)
    result = shell.execute("type /workspace/result.txt")

    assert captured["command"] == f"type {real_workspace}\\result.txt"
    assert result.output == "/workspace/result.txt"


def test_windows_without_sandbox_support_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_mod, "bwrap_usable", lambda: False)
    monkeypatch.setattr(agent_mod, "landlock_usable", lambda: False)
    shell = _PathTranslatingShell(
        path_mappings={"/workspace/": str(tmp_path) + "/"},
        root_dir=tmp_path,
        virtual_mode=True,
        sandbox_enabled=True,
    )
    shell._is_windows = True

    result = shell.execute("dir /workspace")

    assert result.exit_code == 127
    assert "Windows" in result.output
    assert "sandbox_enabled=false" in result.output


def test_windows_deletion_commands_are_recognized():
    file_deletion = SimpleNamespace(
        tool_call={"args": {"command": "del /workspace/file.txt"}}
    )
    directory_deletion = SimpleNamespace(
        tool_call={"args": {"command": "rd /s /q /workspace/dir"}}
    )

    assert agent_mod._is_destructive_command(file_deletion) is True
    assert agent_mod._is_directory_deletion("rd /s /q C:\\dir") is True
    assert agent_mod._is_directory_deletion(
        "powershell Remove-Item -Recurse -Force C:\\dir"
    ) is True


def test_windows_os_info_documents_cmd_shell(monkeypatch):
    monkeypatch.setattr(agent_mod.platform, "system", lambda: "Windows")
    agent = EasyAgent.__new__(EasyAgent)

    os_info = agent._get_os_info()

    assert "## OS: Windows" in os_info
    assert "cmd.exe" in os_info
    assert "python3" in os_info


from pathlib import Path

from easy_agent.agent import _check_external_dir_safety, _is_within, EasyAgent


def test_is_within():
    assert _is_within(Path("/a/b"), Path("/a")) is True
    assert _is_within(Path("/a/b"), Path("/a/b")) is True
    assert _is_within(Path("/a"), Path("/a/b")) is False
    assert _is_within(Path("/x"), Path("/a")) is False


def test_external_dir_safety_rejects_workspace_root(tmp_path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    (ws_root / "alice").mkdir()
    (ws_root / "bob").mkdir()
    # external dir == workspace root -> exposes all users
    ok, reason = _check_external_dir_safety(ws_root, ws_root, "alice")
    assert ok is False
    assert "工作区根" in reason


def test_external_dir_safety_rejects_workspace_ancestor(tmp_path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    # ancestor of the workspace root -> exposes everything
    ok, _reason = _check_external_dir_safety(tmp_path, ws_root, "alice")
    assert ok is False


def test_external_dir_safety_rejects_other_user_subtree(tmp_path):
    ws_root = tmp_path / "workspace"
    bob_dir = ws_root / "bob"
    bob_dir.mkdir(parents=True)
    ok, reason = _check_external_dir_safety(bob_dir, ws_root, "alice")
    assert ok is False
    assert "其他用户" in reason


def test_external_dir_safety_accepts_own_subtree(tmp_path):
    ws_root = tmp_path / "workspace"
    alice_skills = ws_root / "alice" / "skills"
    alice_skills.mkdir(parents=True)
    ok, _reason = _check_external_dir_safety(alice_skills, ws_root, "alice")
    assert ok is True


def test_external_dir_safety_accepts_separate_path(tmp_path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    strategy = tmp_path / "strategy-workspace"
    strategy.mkdir()
    ok, _reason = _check_external_dir_safety(strategy, ws_root, "alice")
    assert ok is True


def test_external_dir_safety_resolves_symlink_to_workspace_root(tmp_path):
    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    link = tmp_path / "link"
    link.symlink_to(ws_root)
    ok, _reason = _check_external_dir_safety(link, ws_root, "alice")
    assert ok is False


def test_build_backend_skips_dangerous_external_dir(tmp_path):
    """外部目录指向工作区根或其他用户子树时，不得挂载为路由/沙箱绑定。"""
    from types import SimpleNamespace

    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    (ws_root / "alice").mkdir()
    (ws_root / "bob").mkdir()
    (ws_root / "bob" / "secret.md").write_text("B-SECRET", encoding="utf-8")

    agent_cfg = SimpleNamespace(
        external_dirs={
            "/strategy-workspace": str(ws_root),            # 工作区根本身（危险）
            "/bob-files": str(ws_root / "bob"),             # 其他用户子树（危险）
            "/safe-shared": str(tmp_path / "shared"),       # 工作区外（安全）
        },
        workspace_dir=str(ws_root),
        sandbox_enabled=True,
    )

    agent = EasyAgent.__new__(EasyAgent)
    agent.config = SimpleNamespace(agent=agent_cfg)
    agent.safe_username = "alice"
    agent.sid = "test"
    agent.workspace_virtual_path = "/workspace"
    agent.workspace_dir = ws_root / "alice" / "session" / "s1"
    agent.user_skills_dir = tmp_path / "nonexistent-skills"
    agent.workspace_dir.mkdir(parents=True, exist_ok=True)

    safe = agent._safe_external_dirs()
    assert "/strategy-workspace/" not in safe
    assert "/bob-files/" not in safe
    assert "/safe-shared/" in safe

    backend = agent._build_backend([], safe)
    route_keys = set(backend.routes.keys())
    assert "/strategy-workspace/" not in route_keys
    assert "/bob-files/" not in route_keys
    assert "/safe-shared/" in route_keys
    assert "/workspace/" in route_keys


def test_build_backend_uses_local_shell_for_each_directory(tmp_path):
    """每个独立目录路由都由 LocalShellBackend 单独承载。"""
    from types import SimpleNamespace

    ws_root = tmp_path / "workspace"
    workspace = ws_root / "alice" / "session" / "s1"
    skills_dir = tmp_path / "skills"
    external_dir = tmp_path / "external"
    workspace.mkdir(parents=True)
    skills_dir.mkdir()
    external_dir.mkdir()

    agent = EasyAgent.__new__(EasyAgent)
    agent.config = SimpleNamespace(
        agent=SimpleNamespace(sandbox_enabled=False)
    )
    agent.workspace_virtual_path = "/workspace"
    agent.workspace_dir = workspace
    agent.user_skills_dir = skills_dir
    agent.sid = "test"

    backend = agent._build_backend(
        ["/user-skills/"],
        {"/external/": str(external_dir)},
    )

    assert isinstance(backend.default, LocalShellBackend)
    assert all(
        type(route_backend) is LocalShellBackend
        for route_backend in backend.routes.values()
    )
    assert backend.routes["/workspace/"].cwd == workspace.resolve()
    assert backend.routes["/user-skills/"].cwd == skills_dir.resolve()
    assert backend.routes["/external/"].cwd == external_dir.resolve()

    backend.write("/workspace/workspace.txt", "workspace")
    backend.write("/user-skills/skills.txt", "skills")
    backend.write("/external/external.txt", "external")

    assert (workspace / "workspace.txt").read_text(encoding="utf-8") == "workspace"
    assert (skills_dir / "skills.txt").read_text(encoding="utf-8") == "skills"
    assert (external_dir / "external.txt").read_text(encoding="utf-8") == "external"
