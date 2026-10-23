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
    """把公共技能目录重定向到临时目录。

    生产上候选目录有多个（CWD 下的 ./skills、项目根下的 ./skills），这里只给一个，
    顺带锁定「候选函数是唯一解析入口」这件事。
    """
    root = tmp_path / "public-skills"
    root.mkdir()
    monkeypatch.setattr(skill_library, "public_skills_dir_candidates", lambda: [root])
    return root


def _make_public_skill(root, name=SKILL_NAME, display_name=None):
    """造一个带子目录的公共技能（验证子文件一起复制）。

    ``display_name`` 用于造「目录英文 + name 中文」的存量技能包。
    """
    skill_dir = root / name
    (skill_dir / "scripts").mkdir(parents=True)
    body = SKILL_BODY
    if display_name is not None:
        body = f"---\nname: {display_name}\ndescription: {display_name}\n---\n\n# {display_name}\n"
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
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
        "provision_expert_skills",
        lambda username, expert_id: (
            calls.append((username, expert_id))
            or {"installed": [SKILL_NAME], "skipped": [], "required": [SKILL_NAME]}
        ),
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
    monkeypatch.setattr(
        manager,
        "provision_expert_skills",
        lambda username, expert_id: {"installed": [], "skipped": [], "required": []},
    )

    a1 = await manager.get_or_create_agent_for_session(
        "sess-noprov", "provuser", "ws-name", model_name="deepseek", enable_web_search=False
    )
    a2 = await manager.get_or_create_agent_for_session(
        "sess-noprov", "provuser", "ws-name", model_name="deepseek", enable_web_search=False
    )

    assert a1 is a2


async def test_missing_skill_is_injected_into_prompt(manager, monkeypatch):
    """技能包缺失（source_missing）→ 必须把「技能未就绪」写进系统提示词末尾。

    线上就是这么翻车的：后端只记了一条 warning，用户只看到模型说
    「/user-skills/ 扫描不到 factor-creator」，无从定位是技能包没放。
    """
    monkeypatch.setattr(
        manager,
        "provision_expert_skills",
        lambda username, expert_id: {
            "required": [SKILL_NAME],
            "installed": [],
            "missing": [SKILL_NAME],
            "skipped": [
                {"skill": SKILL_NAME, "reason": "source_missing", "detail": "已查找: /qts/skills"}
            ],
            "public_dirs": ["/qts/skills"],
        },
    )

    agent = await manager.get_or_create_agent_for_session(
        "sess-missing", "provuser", "ws-name", model_name="deepseek",
        enable_web_search=False, expert_agent_id=EXPERT_ID,
        system_prompt_extra="你是因子研究专家。",
    )

    extra = agent.kwargs["system_prompt_extra"]
    assert "你是因子研究专家。" in extra, "原专家提示词必须保留"
    assert "运行时环境提示" in extra
    assert SKILL_NAME in extra and "不可用" in extra
    assert "不要虚构技能产物" in extra


async def test_healthy_skill_does_not_pollute_prompt(manager, monkeypatch):
    """技能正常（已装/本次装好）时不注入任何运行时提示，避免污染专家提示词。"""
    monkeypatch.setattr(
        manager,
        "provision_expert_skills",
        lambda username, expert_id: {
            "required": [SKILL_NAME],
            "installed": [],
            "missing": [],
            "skipped": [{"skill": SKILL_NAME, "reason": "existing", "detail": ""}],
        },
    )

    agent = await manager.get_or_create_agent_for_session(
        "sess-ok", "provuser", "ws-name", model_name="deepseek",
        enable_web_search=False, expert_agent_id=EXPERT_ID,
        system_prompt_extra="你是因子研究专家。",
    )

    assert agent.kwargs["system_prompt_extra"] == "你是因子研究专家。"


# ── 报告结构与诊断信息 ────────────────────────────────────────────────


def test_report_explains_missing_source(users_root, public_root, monkeypatch):
    """公共目录没有该技能 → 报告给出 source_missing + 找过的路径（现场可直接照着放包）。"""
    _stub_expert(monkeypatch)

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["installed"] == []
    assert report["missing"] == [SKILL_NAME]
    assert report["skipped"][0]["reason"] == "source_missing"
    assert str(public_root) in report["public_dirs"]
    assert public_root.name in report["skipped"][0]["detail"]


def test_runtime_notice_reason_text(users_root, public_root, monkeypatch):
    """三种阻断原因的提示文案各不相同（缺包 / 装失败 / 用户停用）。"""
    for reason, keyword in (
        ("source_missing", "未找到该技能包"),
        ("install_failed", "安装到用户技能库失败"),
        ("disabled", "用户已停用该技能"),
    ):
        notice = skill_provision.build_skill_runtime_notice(
            {"skipped": [{"skill": SKILL_NAME, "reason": reason}]}
        )
        assert keyword in notice, reason
        assert SKILL_NAME in notice


def test_warns_when_skill_package_content_mismatches(users_root, public_root, monkeypatch):
    """目录叫 factor-creator、SKILL.md 却声明别的技能 → 必须报警（实测踩过）。"""
    # 公共目录里同时存在 expert-creator（真正被误拷的那个技能）
    other = public_root / "expert-creator"
    other.mkdir()
    (other / "SKILL.md").write_text(
        "---\nname: expert-creator\ndescription: 创建专家\n---\n", encoding="utf-8"
    )
    # factor-creator 目录里装的是 expert-creator 的内容
    wrong = _make_public_skill(public_root)
    (wrong / "SKILL.md").write_text(
        "---\nname: expert-creator\ndescription: 创建专家\n---\n", encoding="utf-8"
    )
    _stub_expert(monkeypatch)

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["installed"] == [SKILL_NAME], "仍应装上（不阻断），但要报警"
    assert report["warnings"] and "expert-creator" in report["warnings"][0]
    notice = skill_provision.build_skill_runtime_notice(report)
    assert "请先与用户确认技能内容是否正确" in notice


# ── 技能别名：目录名主键 + SKILL.md name 兜底 ─────────────────────────


def test_declared_by_display_name_installs_canonical_dir(users_root, public_root, monkeypatch):
    """存量包「目录英文 + name 中文」：声明写中文名也能装上，落盘目录是目录名。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")
    _make_public_skill(public_root, "precious-metals-analysis", "贵金属投资交易分析")
    _stub_expert(monkeypatch, required=("金市营收情况", "贵金属投资交易分析"))

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    # 装的是 canonical 目录名，不是声明里的中文名
    assert report["installed"] == ["fpp-income-analysis", "precious-metals-analysis"]
    assert _user_skill_path(name="fpp-income-analysis").is_dir()
    assert _user_skill_path(name="precious-metals-analysis").is_dir()
    assert not _user_skill_path(name="金市营收情况").exists(), "不能按展示名建目录"
    # resolved 记录「声明 → 目录」的映射，供排查接口使用
    assert report["resolved"] == [
        {"declared": "金市营收情况", "resolved_dir": "fpp-income-analysis", "matched_by": "name"},
        {
            "declared": "贵金属投资交易分析",
            "resolved_dir": "precious-metals-analysis",
            "matched_by": "name",
        },
    ]


def test_display_name_and_dir_name_both_declared_installs_once(
    users_root, public_root, monkeypatch
):
    """同一技能用两种写法各声明一次：只装一次，第二次记 existing，不产生第二份副本。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")
    _stub_expert(monkeypatch, required=("金市营收情况", "fpp-income-analysis"))

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["installed"] == ["fpp-income-analysis"]
    assert any(item["reason"] == "existing" for item in report["skipped"])
    dirs = [p.name for p in skill_library.user_skills_dir(USERNAME).iterdir() if p.is_dir()]
    assert dirs == ["fpp-income-analysis"]


def test_normalized_dir_name_declaration_resolves(users_root, public_root, monkeypatch):
    """归一化写法（大小写/下划线）也能命中：FPP_Income_Analysis → fpp-income-analysis。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")
    _stub_expert(monkeypatch, required=("FPP_Income_Analysis",))

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["installed"] == ["fpp-income-analysis"]
    assert report["resolved"][0]["matched_by"] == "dir_normalized"


def test_unresolvable_declaration_reports_candidates(users_root, public_root, monkeypatch):
    """解析不到的声明：fail-soft，只给近似候选（仅提示，不自动装）。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")
    _stub_expert(monkeypatch, required=("fpp_income_analysi",))  # 少了结尾的 s

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["missing"] == ["fpp_income_analysi"]
    skipped = report["skipped"][0]
    assert skipped["reason"] == "source_missing"
    assert skipped["candidates"][0]["dir_name"] == "fpp-income-analysis"
    # 候选只用于诊断，绝不据此安装
    assert not skill_library.user_skills_dir(USERNAME).exists()


def test_inspect_one_exposes_resolution_fields(users_root, public_root, monkeypatch):
    """只读探测：声明、解析到的目录、匹配方式、状态都能看到。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")

    item = skill_provision._inspect_one(USERNAME, "金市营收情况")

    assert item["declared"] == "金市营收情况"
    assert item["resolved_dir"] == "fpp-income-analysis"
    assert item["matched_by"] == "name"
    assert item["state"] == "install_ready"
    assert not skill_library.user_skills_dir(USERNAME).exists(), "探测不得写盘"


def test_runtime_notice_mentions_resolved_dir(users_root, public_root, monkeypatch):
    """声明是中文、实际目录是英文时，提示里两个都给，便于用户自查。"""
    _make_public_skill(public_root, "fpp-income-analysis", "金市营收情况")
    disabled = skill_library.user_disabled_dir(USERNAME) / "fpp-income-analysis"
    disabled.mkdir(parents=True)
    (disabled / "SKILL.md").write_text(
        "---\nname: 金市营收情况\ndescription: 金市营收\n---\n", encoding="utf-8"
    )
    _stub_expert(monkeypatch, required=("金市营收情况",))

    report = skill_provision.provision_expert_skills(USERNAME, EXPERT_ID)

    assert report["installed"] == []
    assert [item["reason"] for item in report["skipped"]] == ["disabled"]
    notice = skill_provision.build_skill_runtime_notice(report)
    assert "金市营收情况" in notice
    assert "目录 `fpp-income-analysis`" in notice


# ── 公共技能目录的多候选解析（CWD≠项目根 时也能找到）────────────────────


def test_public_skills_dir_falls_back_to_project_root(monkeypatch, tmp_path):
    """配置写相对路径 ./skills：CWD 下没有、项目根下有 → 必须命中项目根。"""
    project_root = tmp_path / "release"
    (project_root / "skills" / "factor-creator").mkdir(parents=True)
    (project_root / "skills" / "factor-creator" / "SKILL.md").write_text(
        SKILL_BODY, encoding="utf-8"
    )
    elsewhere = tmp_path / "run-dir"
    elsewhere.mkdir()

    from pathlib import Path as _P

    import easy_agent.services.agent_manager as am

    monkeypatch.setattr(am, "_agent_config", {"config": type("C", (), {"tools": type("T", (), {"skills_dir": "./skills"})()})()})
    monkeypatch.setattr(skill_library, "_project_root", lambda: project_root)
    monkeypatch.setattr(_P, "cwd", staticmethod(lambda: elsewhere))

    resolved = skill_library.public_skills_dir()
    assert resolved == project_root / "skills"
    assert skill_library.public_skill_source(SKILL_NAME) == project_root / "skills" / SKILL_NAME


# ── 诊断接口：现场自查「技能为什么没装上」 ────────────────────────────


def test_required_skills_endpoint_reports_status(client):
    """GET /agent/expert/required-skills 返回依赖声明与安装状态（排查入口）。"""
    resp = client.get(
        "/agent/expert/required-skills", params={"expert_id": EXPERT_ID}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["required"] == [SKILL_NAME]
    assert "installed" in data and "skipped" in data and "public_dirs" in data


def test_required_skills_endpoint_404_for_unknown_expert(client):
    resp = client.get(
        "/agent/expert/required-skills", params={"expert_id": "no_such_expert"}
    )
    assert resp.status_code == 404



