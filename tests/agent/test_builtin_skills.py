"""DA-13 S2：公共技能只读共享。

- 启用清单 ``workspace/{user}/builtin-skills.json`` 的增删启停与旧副本迁移
  （``easy_agent/agent/builtin_skills.py``）；
- 只读视图 ``/skills/builtin/``：只看得到已启用的公共技能，写入一律拒绝
  （``easy_agent/backends/builtin_skills.py``）；
- 会话 backend 挂载与权限规则（``agent/backend.py``、``agent/workspace.py``）。
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import types
from pathlib import Path

import pytest

from easy_agent.agent import builtin_skills as store
from easy_agent.agent.backend import _build_backend_for_context
from easy_agent.agent.graph_cache import SessionContext
from easy_agent.backends.builtin_skills import BuiltinSkillsBackend

_fs = pytest.importorskip("deepagents.middleware.filesystem")


def _skill(root: Path, name: str, body: str = "instructions") -> Path:
    skill_dir = root / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n---\n{body}\n", encoding="utf-8"
    )
    (skill_dir / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")
    return skill_dir


@pytest.fixture()
def env(tmp_path):
    canonical = tmp_path / "skills"
    for name in ("docx", "pdf", "xlsx"):
        _skill(canonical, name)
    config = types.SimpleNamespace(
        agent=types.SimpleNamespace(workspace_dir=str(tmp_path / "workspace")),
        tools=types.SimpleNamespace(skills_dir=str(canonical)),
    )
    return types.SimpleNamespace(
        tmp=tmp_path, root=canonical, config=config, user=tmp_path / "workspace" / "alice"
    )


# ── 启用清单 ──────────────────────────────────────────────────────────


def test_add_toggle_remove_round_trip(env):
    added = store.add("alice", "docx", env.root, env.config)
    assert added["enabled"] is True and added["source"] == "builtin"
    assert (env.user / store.STORE_FILE_NAME).is_file()
    assert store.enabled_names("alice", env.root, env.config) == ("docx",)

    with pytest.raises(FileExistsError):
        store.add("alice", "docx", env.root, env.config)
    with pytest.raises(FileNotFoundError):
        store.add("alice", "missing", env.root, env.config)

    store.set_enabled("alice", "docx", False, env.config)
    assert store.enabled_names("alice", env.root, env.config) == ()
    assert [item["enabled"] for item in store.list_added("alice", env.root, env.config)] == [False]

    assert store.remove("alice", "docx", env.config) is True
    assert store.remove("alice", "docx", env.config) is False
    assert store.list_added("alice", env.root, env.config) == []
    # 移除只改清单，公共技能目录不动
    assert (env.root / "docx" / "SKILL.md").is_file()


def test_store_follows_the_given_config_not_the_runtime_one(env, monkeypatch):
    """EasyAgent / 迁移脚本传自己的 config：清单不能落到运行期配置的回退目录 ./workspace 下。"""
    import easy_agent.bootstrap.runtime_config as runtime_config

    monkeypatch.setattr(runtime_config, "_runtime_agent_config", None)
    cwd = env.tmp / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    store.add("alice", "docx", env.root, env.config)
    store.enabled_names("alice", env.root, env.config)

    assert (env.user / store.STORE_FILE_NAME).is_file()
    assert not (cwd / "workspace").exists()


def test_offline_canonical_skill_disappears_from_the_list(env):
    store.add("alice", "docx", env.root, env.config)
    shutil.rmtree(env.root / "docx")
    assert store.list_added("alice", env.root, env.config) == []


@pytest.mark.parametrize(
    "name, safe",
    [
        ("docx", True),
        ("pdf-report_2", True),
        ("", False),
        (".", False),
        ("..", False),
        (".hidden", False),
        ("../alice", False),
        ("a/b", False),
        ("a\\b", False),
        ("a\0b", False),
    ],
)
def test_dir_names_must_be_a_single_path_segment(name, safe):
    assert store.is_safe_dir_name(name) is safe


def test_unsafe_names_are_never_resolved(env):
    """``../其他用户/skills/x`` 既加不进清单，手改清单也不会被当成公共技能。"""
    _skill(env.tmp / "workspace" / "bob" / "skills", "secret")
    with pytest.raises(FileNotFoundError):
        store.add("alice", "../workspace/bob/skills/secret", env.root, env.config)

    store.save_store(
        "alice",
        {"skills": {"../workspace/bob/skills/secret": {"enabled": True}}, "migrated": True},
        env.config,
    )
    assert store.list_added("alice", env.root, env.config) == []


# ── 旧副本迁移 ────────────────────────────────────────────────────────


def _copy_into(env, name: str, folder: str = store.USER_SKILLS_DIR_NAME) -> Path:
    target = env.user / folder / name
    shutil.copytree(env.root / name, target)
    return target


def test_identical_copies_are_migrated_and_modified_ones_kept(env):
    _copy_into(env, "docx")
    _copy_into(env, "pdf", store.USER_DISABLED_DIR_NAME)
    forked = _copy_into(env, "xlsx")
    (forked / "SKILL.md").write_text("my own version\n", encoding="utf-8")
    # 运行技能脚本留下的缓存不算改动
    (env.user / "skills" / "docx" / "scripts" / "__pycache__").mkdir()
    (env.user / "skills" / "docx" / "scripts" / "__pycache__" / "run.cpython-311.pyc").write_bytes(b"x")

    items = {item["dir_name"]: item["enabled"] for item in store.list_added("alice", env.root, env.config)}

    assert items == {"docx": True, "pdf": False}  # 启停状态沿用
    assert not (env.user / "skills" / "docx").exists()
    assert not (env.user / "skills-disabled" / "pdf").exists()
    assert (forked / "SKILL.md").read_text(encoding="utf-8") == "my own version\n"
    saved = json.loads((env.user / store.STORE_FILE_NAME).read_text(encoding="utf-8"))
    assert saved["migrated"] is True and "xlsx" not in saved["skills"]


def test_migration_runs_once(env):
    store.list_added("alice", env.root, env.config)
    copy = _copy_into(env, "docx")
    store.list_added("alice", env.root, env.config)
    assert copy.is_dir()  # 迁移过之后新出现的同名目录是用户自己的技能


def test_preview_changes_nothing(env):
    copy = _copy_into(env, "docx")
    report = store.migrate_user_copies("alice", env.root, apply=False, config=env.config)
    assert report == {"removed": ["docx"], "kept": []}
    assert copy.is_dir()
    assert not (env.user / store.STORE_FILE_NAME).exists()


def test_migration_script_preview_and_apply(env):
    from scripts.skills.migrate_builtin_skills import migrate_all, users_with_skills

    _copy_into(env, "docx")
    workspace = env.tmp / "workspace"
    assert users_with_skills(workspace) == ["alice"]

    preview = migrate_all(["alice"], env.root, apply=False, config=env.config)
    assert preview == [("alice", {"removed": ["docx"], "kept": []})]
    assert (env.user / "skills" / "docx").is_dir()

    migrate_all(["alice"], env.root, apply=True, config=env.config)
    assert not (env.user / "skills" / "docx").exists()
    assert migrate_all(["alice"], env.root, apply=True, config=env.config) == [("alice", None)]


# ── 只读视图 /skills/builtin/ ──────────────────────────────────────────


@pytest.fixture()
def view(env):
    return BuiltinSkillsBackend(env.root, ["docx", "pdf"])


def test_view_lists_and_reads_only_enabled_skills(view):
    assert sorted(entry["path"].strip("/") for entry in view.ls("/").entries) == ["docx", "pdf"]
    assert view.read("/docx/SKILL.md").error is None
    assert view.ls("/xlsx").error
    assert view.read("/xlsx/SKILL.md").error


def test_view_search_results_skip_disabled_skills(view):
    grep_paths = {match["path"].split("/")[1] for match in view.grep("instructions").matches}
    assert grep_paths == {"docx", "pdf"}
    glob_paths = {match["path"].split("/")[1] for match in view.glob("**/SKILL.md").matches}
    assert glob_paths == {"docx", "pdf"}
    assert view.grep("instructions", path="/xlsx").matches == []
    assert view.glob("*.md", path="/xlsx").matches == []


def test_view_downloads_only_enabled_skills(view):
    responses = view.download_files(["/docx/SKILL.md", "/xlsx/SKILL.md"])
    assert responses[0].error is None and b"instructions" in responses[0].content
    assert responses[1].error == "file_not_found"


def test_view_rejects_every_write(view, env):
    before = (env.root / "docx" / "SKILL.md").read_text(encoding="utf-8")
    assert "只读" in view.write("/docx/new.md", "x").error
    assert "只读" in view.edit("/docx/SKILL.md", "instructions", "hacked").error
    assert "只读" in view.delete("/docx/SKILL.md").error
    assert view.upload_files([("/docx/up.md", b"x")])[0].error == "permission_denied"
    assert (env.root / "docx" / "SKILL.md").read_text(encoding="utf-8") == before
    assert not (env.root / "docx" / "new.md").exists()


# ── 会话 backend 挂载与权限 ──────────────────────────────────────────


def _context(env, enabled) -> SessionContext:
    ws = env.user / "session" / "s1"
    ws.mkdir(parents=True, exist_ok=True)
    return SessionContext(
        session_id="s1",
        username="alice",
        safe_username="alice",
        workspace_dir=str(ws),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(env.user / "skills"),
        memory_file=str(ws / "memory.md"),
        long_term_memory_file=str(env.tmp / "memories" / "alice" / "AGENTS.md"),
        builtin_skills_dir=str(env.root),
        builtin_skills=tuple(enabled),
    )


def _session_backend(env, enabled):
    env.config.agent.external_dirs = {}
    env.config.agent.sandbox_enabled = False
    return _build_backend_for_context(env.config, _context(env, enabled), ["/skills/builtin/", "/user-skills/"])


def test_session_backend_mounts_the_read_only_view(env):
    backend = _session_backend(env, ["docx"])
    assert isinstance(backend.routes["/skills/builtin/"], BuiltinSkillsBackend)
    assert backend.read("/skills/builtin/docx/SKILL.md").error is None
    assert backend.read("/skills/builtin/pdf/SKILL.md").error
    # 报错里是模型看到的完整路径，而不是去掉挂载前缀后的 /docx/x.md
    assert "/skills/builtin/docx/x.md" in backend.write("/skills/builtin/docx/x.md", "x").error


def test_no_mount_without_a_builtin_root(env):
    ctx = dataclasses.replace(_context(env, ["docx"]), builtin_skills_dir="", builtin_skills=())
    env.config.agent.external_dirs = {}
    env.config.agent.sandbox_enabled = False
    backend = _build_backend_for_context(env.config, ctx, ["/user-skills/"])
    assert "/skills/builtin/" not in backend.routes


def test_writes_to_builtin_skills_are_denied_by_permissions(monkeypatch, tmp_path):
    """框架在工具层就拦下写入（backend 本身也拒绝，双保险）。"""
    from easy_agent.agent import EasyAgent
    from easy_agent.bootstrap.config import Config

    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    canonical = tmp_path / "skills"
    _skill(canonical, "docx")
    config.tools.skills_dir = str(canonical)
    agent = EasyAgent(
        config=config,
        system_prompt="P",
        username="alice",
        session_id="sess-builtin-perm",
        workspace_dir=tmp_path / "ws",
    )
    rules = agent._build_permissions(agent._resolve_skills_paths(), agent._safe_external_dirs())

    assert _fs._check_fs_permission(rules, "write", "/skills/builtin/docx/SKILL.md") == "deny"
    assert _fs._check_fs_permission(rules, "write", "/skills/builtin/.probe") == "deny"
    assert _fs._check_fs_permission(rules, "read", "/skills/builtin/docx/SKILL.md") == "allow"
    assert agent._resolve_skills_paths() == ["/skills/builtin/", "/user-skills/"]
