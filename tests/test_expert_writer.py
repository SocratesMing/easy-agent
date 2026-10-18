"""对话内创建 / 修改专家的工具（``tools/expert_writer.py``）测试。

覆盖点：

| # | 场景 | 预期 |
|---|---|---|
| 1 | 结构化直传创建 | 落库 active、写 v1 版本目录 + active 指针、不再调模型 |
| 2 | 缺 system_prompt | 退化为调用模型兜底生成（慢路径）并可成功 |
| 3 | 中文名重名 | 返回「错误：」话术，不写库、不写目录 |
| 4 | 缺名称 / 缺描述 | 返回「错误：」话术（参数校验前置于落库） |
| 5 | tag_id 归一 | 中文标签名也能归一成候选 tag_id；不在候选内则清空 |
| 6 | 修改 | 写 v{n+1}、旧版本保留、active 指向新版本、缓存驱逐被调用 |
| 7 | 修改越权 / 无 ID | 返回「错误：」话术，他人的专家改不动 |
| 8 | 修改重名 | 返回「错误：」话术，库记录不变 |
| 9 | 提示词结构 | 生成的 md 含 frontmatter + 核心能力/工作流程/输出规范/注意事项 + 简体中文 |
| 10 | 文案不外泄 | 返回给模型的文本不含服务端版本目录路径 |
| 11 | 工具注入 | 会话 Agent 注入 save_expert，且描述里带配置的分类标签候选 |

用例离线：不连网、不调真实模型（``generate_expert_config`` / ``create_model`` 均被打桩）。
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.db.expert_store import STATUS_ACTIVE, STATUS_FAILED, ExpertStore
from easy_agent.db import init_database
from easy_agent.services.expert_service import get_expert_dir
import easy_agent.tools.expert_writer as writer

TAG_OPTIONS = [
    {"tag_id": "macro_research", "tag_name": "宏观投研"},
    {"tag_id": "credit_research", "tag_name": "信用研究"},
    {"tag_id": "other", "tag_name": "其他"},
]

FAKE_CONFIG = {
    "scenario": "面向信用债投资与研究工作。",
    "capabilities": ["主体基本面评估", "利差分析"],
    "workflow": ["明确对象", "梳理基本面", "给出结论"],
    "boundaries": ["不编造数据"],
    "system_prompt": "你是「信用债投研专家」，一位有 8 年信用债研究经验的资深研究员。",
    "features_prompt": ["帮我评估这家城投主体", "这两个产业债哪个更好"],
    "description": "从主体基本面与利差水平评估信用债风险并给出结论式判断。",
    "skills_names": ["债券投研", "信用研究", "风险控制"],
    "tag_id": "credit_research",
    "name_en": "credit-bond-researcher",
    "profession_zh": "资深信用债研究员",
    "profession_en": "Senior Credit Bond Researcher",
    "output_spec": ["先结论后依据", "标注数据来源"],
}


@pytest.fixture()
def env(db, monkeypatch, tmp_path):
    """把专家目录重定向到 tmp、把工具的 DB / 配置换成测试桩。"""
    monkeypatch.setattr(
        Config,
        "get_user_workspace_dir",
        staticmethod(lambda username: tmp_path / "users" / username),
    )
    monkeypatch.setattr(writer, "get_database", lambda: db)
    monkeypatch.setattr(
        writer,
        "_agent_config",
        lambda: SimpleNamespace(expert_tags=TAG_OPTIONS, active_model="mock-model"),
    )
    # 兜底生成路径里的 create_model 也要打桩（不会真的用到，但避免误连网）
    import easy_agent.agent.model as model_mod

    monkeypatch.setattr(model_mod, "create_model", lambda *a, **k: object())
    return SimpleNamespace(db=db, tmp=tmp_path)


def _make_tool(**kwargs) -> writer.SaveExpertTool:
    params = {"username": "testuser", "session_id": "s1"}
    params.update(kwargs)
    return writer.create_save_expert_tool(**params)


def _call(tool, **kwargs) -> str:
    return asyncio.run(tool._arun(**kwargs))


def _create(tool, *, name_cn="信用债投研专家", prompt="8 年信用债研究经验", **extra) -> str:
    params = {
        "action": "create",
        "name_cn": name_cn,
        "prompt": prompt,
        **{k: v for k, v in FAKE_CONFIG.items()},
    }
    params.update(extra)
    return _call(tool, **params)


def _active_md_files(username: str, expert_id: str) -> list[Path]:
    version_dir = get_expert_dir(username, expert_id) / "v1"
    return [p for p in version_dir.glob("*.md") if p.name.lower() != "readme.md"]


# ── 1. 结构化直传创建 ──────────────────────────────────────────────────


def test_create_structured_persists(env, monkeypatch):
    """结构化字段齐全时直接落盘，且**不再调模型**（快路径）。"""
    called = {"n": 0}

    async def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("结构化直传不应调用 generate_expert_config")

    monkeypatch.setattr(writer, "generate_expert_config", _boom)

    out = _create(_make_tool())
    assert out.startswith("专家创建成功！")
    assert called["n"] == 0

    store = ExpertStore(env.db)
    rows = store.list_by_user("testuser")[1]
    assert len(rows) == 1
    record = rows[0]
    assert record["agent_status"] == STATUS_ACTIVE
    assert record["export_name_cn"] == "信用债投研专家"
    assert record["export_version"] == 1
    assert record["export_skills_names"] == ["债券投研", "信用研究", "风险控制"]
    assert record["tag_id"] == "credit_research"
    assert record["export_name_en"] == "credit-bond-researcher"

    expert_dir = get_expert_dir("testuser", record["export_agent_id"])
    assert (expert_dir / "active").read_text(encoding="utf-8").strip() == "1"
    assert (expert_dir / "v1" / "config.yaml").is_file()
    assert (expert_dir / "v1" / "manifest.json").is_file()


def test_create_prompt_structure_and_language(env):
    """生成的提示词 md 结构完整（与内置预置专家同一套守门断言）。"""
    out = _create(_make_tool())
    expert_id = _expert_id_from(out)
    md_files = _active_md_files("testuser", expert_id)
    assert len(md_files) == 1
    text = md_files[0].read_text(encoding="utf-8")

    assert text.startswith("---\n")
    assert "角色设定" in text
    for section in ("## 核心能力", "## 工作流程", "## 输出规范", "## 注意事项"):
        assert section in text


def _expert_id_from(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("- 专家ID："):
            return line.split("：", 1)[1].strip()
    raise AssertionError(f"返回文本里没有专家 ID：{text}")


# ── 2. 兜底生成（慢路径） ──────────────────────────────────────────────


def test_create_without_system_prompt_falls_back_to_llm(env, monkeypatch):
    """没给 system_prompt 时退化为调用模型补齐配置，仍能创建成功。"""
    captured: dict = {}

    async def _fake_generate(llm, name_cn, user_prompt, max_retries=1, **kwargs):
        captured.update(name_cn=name_cn, user_prompt=user_prompt, **kwargs)
        return dict(FAKE_CONFIG)

    monkeypatch.setattr(writer, "generate_expert_config", _fake_generate)

    out = _call(
        _make_tool(),
        action="create",
        name_cn="信用债投研专家",
        prompt="8 年信用债研究经验，关注城投与产业债",
    )
    assert "创建成功" in out
    assert "由模型自动生成" in out
    assert captured["name_cn"] == "信用债投研专家"
    assert "城投" in captured["user_prompt"]
    # 候选标签透传给生成器（模型据此归类）
    assert captured["tag_options"] == TAG_OPTIONS


def test_create_llm_failure_marks_failed(env, monkeypatch):
    """兜底生成失败：返回错误话术，且库里留下 failed 记录供排查/重试。"""

    async def _boom(*a, **k):
        raise ValueError("模型返回不是 JSON")

    monkeypatch.setattr(writer, "generate_expert_config", _boom)

    out = _call(_make_tool(), action="create", name_cn="失败专家", prompt="描述")
    assert out.startswith("错误：")
    assert "生成失败" in out

    rows = ExpertStore(env.db).list_by_user("testuser", status=STATUS_FAILED)[1]
    assert len(rows) == 1
    assert rows[0]["agent_status"] == STATUS_FAILED


# ── 3/4. 参数与重名拦截 ────────────────────────────────────────────────


def test_create_duplicate_name_rejected(env):
    tool = _make_tool()
    assert "创建成功" in _create(tool, name_cn="信用债投研专家")
    # 第二次同名（含空白差异）应被拦下
    out = _create(tool, name_cn=" 信用债投研 专家 ")
    assert out.startswith("错误：")
    assert "同名" in out
    assert ExpertStore(env.db).list_by_user("testuser")[0] == 1


def test_create_missing_name_or_prompt_rejected(env):
    tool = _make_tool()
    assert _call(tool, action="create", name_cn="  ", prompt="描述").startswith("错误：")
    assert _call(tool, action="create", name_cn="专家", prompt="").startswith("错误：")
    # 一条记录都不该落
    assert ExpertStore(env.db).list_by_user("testuser")[0] == 0


def test_create_invalid_args_rejected(env):
    """缺 action（Literal 校验失败）时返回可读错误，不抛异常。"""
    out = _call(_make_tool(), action="delete", name_cn="x", prompt="y")
    assert out.startswith("错误：")

    out2 = _call(_make_tool(), name_cn="x", prompt="y")
    assert out2.startswith("错误：")


# ── 5. 分类标签归一 ────────────────────────────────────────────────────


def test_tag_id_accepts_chinese_name_and_rejects_unknown(env):
    tool = _make_tool()
    _create(tool, name_cn="甲专家", tag_id="信用研究")  # 传中文标签名
    _create(tool, name_cn="乙专家", tag_id="不存在的标签")

    rows = {r["export_name_cn"]: r for r in ExpertStore(env.db).list_by_user("testuser")[1]}
    assert rows["甲专家"]["tag_id"] == "credit_research"
    assert rows["乙专家"]["tag_id"] == ""


def test_tag_options_fall_back_to_runtime_config(env, monkeypatch):
    """构造时未带标签候选（老调用方）：调用时回读运行期配置。"""
    tool = writer.SaveExpertTool(username="testuser", session_id="s1")
    assert tool.tag_options == []
    _create(tool, name_cn="丙专家", tag_id="宏观投研")
    rows = ExpertStore(env.db).list_by_user("testuser")[1]
    assert rows[0]["tag_id"] == "macro_research"


# ── 6/7/8. 修改 ────────────────────────────────────────────────────────


def test_update_writes_next_version_and_keeps_previous(env, monkeypatch):
    tool = _make_tool()
    out = _create(tool)
    expert_id = _expert_id_from(out)

    invalidated = {"n": 0}
    from easy_agent.bootstrap import runtime_config

    monkeypatch.setattr(
        runtime_config,
        "invalidate_user_agents",
        lambda u: invalidated.__setitem__("n", invalidated["n"] + 1),
    )

    out2 = _call(
        tool,
        action="update",
        expert_agent_id=expert_id,
        name_cn="信用债投研专家（升级）",
        prompt="在原有经验上增加城投再融资压力排查",
        **FAKE_CONFIG,
    )
    assert "修改成功" in out2
    assert "v2" in out2

    expert_dir = get_expert_dir("testuser", expert_id)
    assert (expert_dir / "v1").is_dir()      # 旧版本保留
    assert (expert_dir / "v2").is_dir()
    assert (expert_dir / "active").read_text(encoding="utf-8").strip() == "2"

    record = ExpertStore(env.db).get(expert_id)
    assert record["export_name_cn"] == "信用债投研专家（升级）"
    assert record["export_version"] == 2
    assert invalidated["n"] == 1


def test_update_keeps_original_fields_when_not_provided(env):
    """修改时只传增量：名称 / 原始描述沿用旧值，未提及的字段不丢。"""
    tool = _make_tool()
    expert_id = _expert_id_from(_create(tool, prompt="原始需求：8 年信用债经验"))

    out = _call(tool, action="update", expert_agent_id=expert_id, **FAKE_CONFIG)
    assert "修改成功" in out
    record = ExpertStore(env.db).get(expert_id)
    assert record["export_name_cn"] == "信用债投研专家"
    assert record["export_prompt"] == "原始需求：8 年信用债经验"


def test_update_unknown_or_foreign_expert_rejected(env):
    tool = _make_tool()
    assert _call(tool, action="update", expert_agent_id="expert_nope").startswith("错误：")
    assert _call(tool, action="update").startswith("错误：")

    # 别人的专家：直接插一条归属其它 user_id 的记录
    store = ExpertStore(env.db)
    store.create(
        {
            "export_agent_id": "expert_other_0001",
            "user_id": "someone-else",
            "export_name_en": "others-expert",
            "export_name_cn": "别人的专家",
            "export_prompt": "x",
            "agent_status": STATUS_ACTIVE,
        }
    )
    out = _call(tool, action="update", expert_agent_id="expert_other_0001", **FAKE_CONFIG)
    assert out.startswith("错误：")
    assert "不属于当前用户" in out
    assert store.get("expert_other_0001")["export_name_cn"] == "别人的专家"


def test_update_duplicate_name_rejected(env):
    tool = _make_tool()
    first = _expert_id_from(_create(tool, name_cn="甲专家"))
    _create(tool, name_cn="乙专家")

    out = _call(
        tool,
        action="update",
        expert_agent_id=first,
        name_cn="乙专家",
        prompt="改名撞车",
        **FAKE_CONFIG,
    )
    assert out.startswith("错误：")
    assert "同名" in out
    assert ExpertStore(env.db).get(first)["export_name_cn"] == "甲专家"


# ── 10. 文案 ───────────────────────────────────────────────────────────


def test_result_text_hides_server_paths(env):
    """返回给模型的文本不得包含服务端版本目录路径（用户可见文案的来源）。"""
    out = _create(_make_tool())
    expert_id = _expert_id_from(out)
    version_dir = str(get_expert_dir("testuser", expert_id))
    assert version_dir not in out
    assert "v1/" not in out.replace("版本：v1", "")
    assert "config.yaml" not in out
    assert "manifest.json" not in out
    assert "专家 · 技能 · 连接器" in out  # 引导用户去面板里使用


# ── 11. 工具注入 ───────────────────────────────────────────────────────


def test_agent_manager_injects_save_expert(client, monkeypatch):
    """会话 Agent 应注入 save_expert，且工具描述携带配置里的分类标签候选。"""
    import easy_agent.agent.core as agent_mod
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")

    # 测试配置里没有 expert_tags，这里补上以验证「候选标签写进工具描述」的行为
    monkeypatch.setattr(am._agent_config["config"], "expert_tags", TAG_OPTIONS, raising=False)

    captured: dict = {}
    monkeypatch.setattr(
        agent_mod,
        "create_deep_agent",
        lambda **kwargs: (captured.update(kwargs), {"fake": True})[1],
    )

    asyncio.run(
        am.get_or_create_agent_for_session(
            "sess-writer-tool-1", username="testuser", enable_hitl=False
        )
    )

    tools = {getattr(t, "name", ""): t for t in (captured.get("tools") or [])}
    assert "save_expert" in tools
    assert "credit_research: 信用研究" in tools["save_expert"].description
    assert tools["save_expert"].tag_options == TAG_OPTIONS


def test_tool_is_registered_with_db_independent_username(db):
    """工具构造本身不触碰数据库（懒加载）：仅构造不应抛异常。"""
    tool = writer.SaveExpertTool(username="u", session_id="s")
    assert tool.name == "save_expert"
    assert tool.config_model == ""
    assert tool.tag_options == []


# ── 与 API 路径的一致性守门 ─────────────────────────────────────────────


def test_created_expert_matches_api_layout(env, client):
    """对话创建的专家能被 API 列表接口看到（同一张表、同一套字段口径）。"""
    expert_id = _expert_id_from(_create(_make_tool()))

    resp = client.get("/agent/list")
    assert resp.status_code == 200
    items = resp.json()["items"]
    hit = [i for i in items if i["export_agent_id"] == expert_id]
    assert len(hit) == 1
    assert hit[0]["agent_status"] == STATUS_ACTIVE
    assert hit[0]["export_skills_names"] == ["债券投研", "信用研究", "风险控制"]


def test_db_fixture_is_isolated(tmp_path):
    """兜底自检：init_database 建表可用（防止上面的 db fixture 被误改）。"""
    from easy_agent.db.database import Database

    database: Database = init_database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "x.db")}}
    )
    assert ExpertStore(database).list_by_user("u")[0] == 0


# ── 12. 待修改专家绑定（ID 不进消息体，防篡改） ─────────────────────────


def test_update_uses_bound_expert_id_without_args(env):
    """系统绑定的待修改专家：模型不传 expert_agent_id 也能改（ID 由系统注入）。"""
    expert_id = _expert_id_from(_create(_make_tool(), name_cn="信用债投研专家"))

    bound_tool = _make_tool(edit_targets={"s1": expert_id})
    out = _call(bound_tool, action="update", name_cn="信用债投研专家V2", **FAKE_CONFIG)
    assert "修改成功" in out

    record = ExpertStore(env.db).get(expert_id)
    assert record["export_name_cn"] == "信用债投研专家V2"
    assert record["export_version"] == 2


def test_update_rejects_id_mismatch_with_bound_target(env):
    """模型传入的 ID 与系统绑定值不一致 → 拒绝（消息体里的 ID 不可信）。"""
    first = _expert_id_from(_create(_make_tool(), name_cn="专家甲"))
    second = _expert_id_from(_create(_make_tool(), name_cn="专家乙"))

    tool = _make_tool(edit_targets={"s1": first})
    out = _call(tool, action="update", expert_agent_id=second, **FAKE_CONFIG)
    assert out.startswith("错误：")
    assert "不一致" in out
    # 双方都没有被改动
    assert ExpertStore(env.db).get(first)["export_version"] == 1
    assert ExpertStore(env.db).get(second)["export_version"] == 1


def test_update_without_bound_or_supplied_id_rejected(env):
    """既无系统绑定、模型也没传 ID：拒绝并引导用户去专家面板点「编辑」。"""
    out = _call(_make_tool(), action="update", **FAKE_CONFIG)
    assert out.startswith("错误：")
    assert "编辑" in out


def test_resolve_owned_expert_rejects_foreign_or_inactive(env):
    """绑定前的归属校验：只接受本人创建的可用专家。"""
    expert_id = _expert_id_from(_create(_make_tool()))
    store = ExpertStore(env.db)

    assert writer.resolve_owned_expert(env.db, "testuser", expert_id) is not None
    # 不存在的 ID（预置专家不落库，同样查不到）
    assert writer.resolve_owned_expert(env.db, "testuser", "expert_nope") is None
    assert writer.resolve_owned_expert(env.db, "testuser", "") is None
    # 已停用的专家不可作为修改目标
    store.update_fields(expert_id, {"agent_status": "disabled"})
    assert writer.resolve_owned_expert(env.db, "testuser", expert_id) is None


def test_created_prompt_md_contains_boundary_section(env):
    """落盘的提示词 md 程序化携带「职责边界」段（不依赖 LLM 按模板生成）。"""
    out = _create(_make_tool())
    expert_id = _expert_id_from(out)
    text = _active_md_files("testuser", expert_id)[0].read_text(encoding="utf-8")

    assert "## 职责边界" in text
    assert "请提问我领域内的问题" in text
    assert "不编造" in text


# ── 13. save_expert 条件注入 + 绑定表（agent_manager） ──────────────────


def _stub_deep_agent(monkeypatch, captured: list):
    import easy_agent.agent.core as agent_mod

    def _fake(**kwargs):
        captured.append(kwargs)
        return {"fake": True}

    monkeypatch.setattr(agent_mod, "create_deep_agent", _fake)


def _tool_names(kwargs) -> set:
    return {getattr(t, "name", "") for t in (kwargs.get("tools") or [])}


def test_agent_manager_save_expert_only_with_creator_skill(client, monkeypatch):
    """save_expert 仅在 expert-creator 技能激活的会话注入，其他会话不带。"""
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")

    captured: list = []
    _stub_deep_agent(monkeypatch, captured)

    asyncio.run(
        am.get_or_create_agent_for_session(
            "sess-creator-on", username="testuser", enable_hitl=False,
            skill_dirs=["expert-creator"],
        )
    )
    asyncio.run(
        am.get_or_create_agent_for_session(
            "sess-creator-off", username="testuser", enable_hitl=False,
            skill_dirs=["docx"],
        )
    )

    assert "save_expert" in _tool_names(captured[0])
    assert "save_expert" not in _tool_names(captured[1])


def test_agent_manager_evicts_when_creator_flag_changes(client, monkeypatch):
    """save_expert 需求变化（带 -> 不带）必须驱逐重建，否则工具集黏住。"""
    import easy_agent.services.agent_manager as am

    if not am._agent_config or not am._agent_config.get("config"):
        pytest.skip("全局 Agent 配置未初始化")

    captured: list = []
    _stub_deep_agent(monkeypatch, captured)

    sid = "sess-creator-flip"
    asyncio.run(
        am.get_or_create_agent_for_session(
            sid, username="testuser", enable_hitl=False, skill_dirs=["expert-creator"]
        )
    )
    asyncio.run(
        am.get_or_create_agent_for_session(
            sid, username="testuser", enable_hitl=False, skill_dirs=[]
        )
    )

    assert len(captured) == 2
    assert "save_expert" in _tool_names(captured[0])
    assert "save_expert" not in _tool_names(captured[1])


def test_session_edit_target_bind_override_and_clear(client):
    """绑定表：新绑定覆盖旧绑定、随会话 Agent 清理一起清除。"""
    import easy_agent.services.agent_manager as am

    am.set_session_edit_target("sess-edit-bind", "expert_a_1")
    am.set_session_edit_target("sess-edit-bind", "expert_b_2")
    assert am.get_session_edit_target("sess-edit-bind") == "expert_b_2"

    am.remove_session_agent("sess-edit-bind")
    assert am.get_session_edit_target("sess-edit-bind") == ""

    # 空值不写入
    am.set_session_edit_target("sess-edit-bind2", "  ")
    assert am.get_session_edit_target("sess-edit-bind2") == ""
