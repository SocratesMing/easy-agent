"""专家依赖技能自动补装（``services/skill_provision``）的行为约束。

背景：因子研究专家在 ``config.yaml`` 里声明 ``required_skills: [factor-creator]``，
用户第一次用该专家时要把技能从公共技能目录装进自己的技能库，否则专家提示词里的
「技能优先」协议会静默降级成「技能不存在」。

这里锁定四件事：装得上、不重复装、不覆盖用户的停用意图、失败不抛异常。
"""

import pytest

from easy_agent.config import Config
from easy_agent.services import public_expert_service, skill_library, skill_provision

USERNAME = "provuser"
EXPERT_ID = "preset_factor_research"
SKILL_NAME = "factor-creator"

SKILL_BODY = "---\nname: factor-creator\ndescription: 创建因子\n---\n\n# 因子创建\n"


@pytest.fixture()
def users_root(monkeypatch, tmp_path):
    """把用户工作区重定向到临时目录，避免污染仓库。"""
    root = tmp_path / "workspace" / "users"
    monkeypatch.setattr(
        Config, "get_user_workspace_dir", staticmethod(lambda username: root / username)
    )
    return root


@pytest.fixture()
def public_root(monkeypatch, tmp_path):
    """把公共技能目录重定向到临时目录。"""
    root = tmp_path / "public-skills"
    root.mkdir()
    monkeypatch.setattr(skill_library, "public_skills_dir", lambda: root)
    return root


def _make_public_skill(root, name=SKILL_NAME):
    """造一个带子目录的公共技能（验证子文件一起复制）。"""
    skill_dir = root / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
    (skill_dir / "scripts" / "new_factor.py").write_text("print('ok')\n", encoding="utf-8")
    return skill_dir


def _stub_expert(monkeypatch, required=(SKILL_NAME,)):
    """把公共专家依赖查询替换成固定返回（不依赖仓库 experts/ 目录内容）。"""
    monkeypatch.setattr(
        public_expert_service,
        "required_skills_of_public_expert",
        lambda expert_id: list(required) if expert_id == EXPERT_ID else [],
    )


def _user_skill_path(username=USERNAME, name=SKILL_NAME):
    return skill_library.user_skills_dir(username) / name


# ── 1. 缺失即安装 ─────────────────────────────────────────────────────


def test_installs_missing_required_skill(users_root, public_root, monkeypatch):
    """用户没装技能 → 复制进 skills/（子文件一起），返回实际安装的技能名。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)

    installed = skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID)

    assert installed == [SKILL_NAME]
    target = _user_skill_path()
    assert (target / "SKILL.md").exists()
    assert (target / "scripts" / "new_factor.py").exists(), "子文件应一起复制"
    # 启用目录里可见、停用目录里没有
    found = skill_library.find_user_skill(USERNAME, SKILL_NAME)
    assert found and found["enabled"] is True


# ── 2. 已启用不重复装 ─────────────────────────────────────────────────


def test_skips_when_already_enabled(users_root, public_root, monkeypatch):
    """已启用 → 返回空列表，且不覆盖用户目录里的内容。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == [SKILL_NAME]
    marker = _user_skill_path() / "USER_EDIT.md"
    marker.write_text("用户自己改过", encoding="utf-8")

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []
    assert marker.exists(), "已存在的技能不应被覆盖"


# ── 3. 尊重用户停用 ───────────────────────────────────────────────────


def test_respects_user_disabled_skill(users_root, public_root, monkeypatch):
    """停在 skills-disabled/ 里 → 不装回、不启用，返回空列表。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)
    disabled = skill_library.user_disabled_dir(USERNAME) / SKILL_NAME
    disabled.mkdir(parents=True)
    (disabled / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []
    assert not _user_skill_path().exists(), "不应在启用目录里创建副本"
    assert (disabled / "SKILL.md").exists(), "停用状态应保持原样"


# ── 4. 公共目录缺失不抛异常 ───────────────────────────────────────────


def test_missing_public_skill_does_not_raise(users_root, public_root, monkeypatch):
    """公共技能目录里没有该技能 → 返回空列表（专家降级运行，不阻断对话）。"""
    _stub_expert(monkeypatch)

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []
    assert not _user_skill_path().exists()


# ── 5. 幂等 / 并发安全 ────────────────────────────────────────────────


def test_second_call_is_noop_and_leaves_no_temp_dir(users_root, public_root, monkeypatch):
    """连续两次调用只留一份技能，且不留 .tmp_* 临时目录。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == [SKILL_NAME]
    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []

    leftovers = [
        p.name for p in skill_library.user_skills_dir(USERNAME).iterdir()
        if p.name.startswith(".tmp_")
    ]
    assert leftovers == [], f"不应残留临时目录: {leftovers}"


def test_install_raises_file_exists_for_existing_target(users_root, public_root):
    """底层安装函数在目标已存在时抛 FileExistsError（供接口层转 409）。"""
    _make_public_skill(public_root)
    skill_library.install_public_skill_to_user(USERNAME, SKILL_NAME)

    with pytest.raises(FileExistsError):
        skill_library.install_public_skill_to_user(USERNAME, SKILL_NAME)


# ── 6. 开关关闭 ──────────────────────────────────────────────────────


def test_switch_off_disables_auto_install(users_root, public_root, monkeypatch):
    """auto_install_required_skills=false → 完全不动作。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)
    monkeypatch.setattr(skill_provision, "_auto_install_enabled", lambda: False)

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []
    assert not _user_skill_path().exists()


# ── 边界：非公共专家 / 无声明 ─────────────────────────────────────────


def test_no_expert_or_unknown_expert_is_noop(users_root, public_root, monkeypatch):
    """通用 Agent（无专家）与未知专家都不触发安装。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch)

    assert skill_provision.ensure_expert_skills(USERNAME, None) == []
    assert skill_provision.ensure_expert_skills(USERNAME, "") == []
    assert skill_provision.ensure_expert_skills(USERNAME, "my_own_expert") == []
    assert not _user_skill_path().exists()


def test_expert_without_required_skills_is_noop(users_root, public_root, monkeypatch):
    """专家没声明 required_skills → 不动技能库。"""
    _make_public_skill(public_root)
    _stub_expert(monkeypatch, required=())

    assert skill_provision.ensure_expert_skills(USERNAME, EXPERT_ID) == []


# ── 真实预置专家：声明能被 loader 读出来 ─────────────────────────────


def test_preset_factor_expert_declares_factor_creator():
    """真实目录：preset_factor_research 的 required_skills 必须被 loader 透出。"""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    expert_dir = repo_root / "experts" / "preset_factor_research"
    item = public_expert_service.read_public_expert(expert_dir)

    assert item is not None
    assert item["export_required_skills"] == [SKILL_NAME]
    # 展示标签与依赖技能是两回事，不能混
    assert "factor-creator" not in item["export_skills_names"]

    # 每轮对话走的轻量通道（只读 config.yaml，不读提示词）结果必须一致
    assert public_expert_service.required_skills_of_public_expert(EXPERT_ID) == [SKILL_NAME]
    # 非公共专家（「我的专家」ID）不应命中，也不应抛异常
    assert public_expert_service.required_skills_of_public_expert("my_own_expert") == []


# ── 挂载点：创建 Agent 时必须先补装、并驱逐旧缓存 ─────────────────────


class _FakeAgent:
    """替身 EasyAgent：只需被缓存驱逐判断读取的属性。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_name = kwargs.get("model_name") or "deepseek"
        self.enable_web_search = bool(kwargs.get("enable_web_search"))
        self.system_prompt_extra = kwargs.get("system_prompt_extra", "")
        self.workspace_dir = "/tmp/ws"
        self.safe_username = Config.sanitize_username(kwargs.get("username") or "")


@pytest.fixture()
def manager(db, monkeypatch, tmp_path):
    """把 agent_manager 的外部依赖换成本地假实现（不建真实 Agent / 不连 MCP）。"""
    from types import SimpleNamespace

    import easy_agent.services.agent_manager as am

    am._session_agents.clear()
    am._session_expert.clear()
    am._session_connectors.clear()
    am._session_creator.clear()

    monkeypatch.setattr(am, "EasyAgent", _FakeAgent)
    monkeypatch.setattr(am, "get_database", lambda: db)

    async def _no_mcp(_user=None, server_names=None):
        return []

    monkeypatch.setattr(am, "load_mcp_tools_for_user", _no_mcp)
    monkeypatch.setattr(
        am,
        "_agent_config",
        {
            "config": SimpleNamespace(
                active_model="deepseek",
                agent=SimpleNamespace(workspace_dir=str(tmp_path)),
                expert_auto_install_required_skills=True,
            ),
            "system_prompt": "SYS",
            "skills_root": "",
        },
    )
    yield am
    am._session_agents.clear()
    am._session_expert.clear()
    am._session_creator.clear()


async def test_agent_creation_provisions_skill_and_evicts_cache(manager, monkeypatch):
    """缓存里已有旧 Agent 时，补装技能后必须驱逐重建（否则新技能永远不生效）。"""
    calls = []
    monkeypatch.setattr(
        manager,
        "ensure_expert_skills",
        lambda username, expert_id: (calls.append((username, expert_id)) or [SKILL_NAME]),
    )

    old = _FakeAgent(username="provuser", model_name="deepseek")
    manager._session_agents["sess-prov"] = old
    manager._session_expert["sess-prov"] = EXPERT_ID

    new_agent = await manager.get_or_create_agent_for_session(
        "sess-prov",
        "provuser",
        "ws-name",
        model_name="deepseek",
        enable_web_search=False,
        expert_agent_id=EXPERT_ID,
    )

    assert calls == [("provuser", EXPERT_ID)]
    assert new_agent is not old, "补装后应重建 Agent"


async def test_provision_runs_before_cache_lookup(manager, monkeypatch):
    """已缓存的通用 Agent 不因「无需补装」被驱逐（补装返回空 = 不动缓存）。"""
    monkeypatch.setattr(manager, "ensure_expert_skills", lambda username, expert_id: [])

    a1 = await manager.get_or_create_agent_for_session(
        "sess-noprov", "provuser", "ws-name", model_name="deepseek", enable_web_search=False
    )
    a2 = await manager.get_or_create_agent_for_session(
        "sess-noprov", "provuser", "ws-name", model_name="deepseek", enable_web_search=False
    )

    assert a1 is a2

