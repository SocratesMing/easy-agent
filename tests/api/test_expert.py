"""接口 /agent/{add,update,list,del,stream_export_run} 的测试。

LLM 全程用假实现打桩，不发起任何网络请求；磁盘写入落在临时工作区。
"""

import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import easy_agent.api.chat as chat_api
import easy_agent.api.expert as expert_api
import easy_agent.services.agent_manager as am
import easy_agent.services.skill_library as skill_library
from easy_agent.db.expert_store import ExpertStore
from easy_agent.models.db import SessionModel
from easy_agent.services import expert_relevance
from easy_agent.services.expert_service import load_active_prompt

WORKSPACE_USER_DIR = Path(os.environ["TEST_WORKSPACE_DIR"]) / "users" / "testuser"

FAKE_CONFIG = {
    "scenario": "用于量化选股与因子研究",
    "capabilities": ["因子挖掘", "回测验证"],
    "workflow": ["明确目标", "构造因子", "回测"],
    "boundaries": ["不编造未提供的行情数据"],
    "system_prompt": "你是量化策略专家，擅长因子选股。",
    "features_prompt": ["帮我评估这组因子的有效性", "帮我设计多因子模型"],
    "description": "面向量化投研场景，完成因子挖掘与回测验证，输出可复用的策略结论。",
    "skills_names": ["因子分析", "策略回测", "风险控制"],
    "tag_id": "factor_analysis",
    "name_en": "quant-factor-advisor",
    "profession_zh": "资深量化策略研究员",
    "profession_en": "Senior Quantitative Researcher",
    "output_spec": ["先结论后论证", "数据注明来源与时间"],
}


@pytest.fixture()
def fake_llm(monkeypatch):
    """打桩 create_model + generate_expert_config，记录生成次数。"""
    state = {"generate_calls": 0}

    monkeypatch.setattr(
        expert_api, "create_model", lambda *a, **k: SimpleNamespace(name="fake-llm")
    )

    async def _fake_generate(llm, name_cn, user_prompt, max_retries=1, **kwargs):
        state["generate_calls"] += 1
        return dict(FAKE_CONFIG)

    monkeypatch.setattr(expert_api, "generate_expert_config", _fake_generate)
    return state


def _add(client, name="量化策略专家", prompt="帮我创建一个量化策略专家，擅长因子选股"):
    return client.post("/agent/add", json={"type": "agent", "name_cn": name, "prompt": prompt})


# ── 建表 ──────────────────────────────────────────────────────────────


def test_expert_table_exists_in_app_db(client, db):
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        assert "expert_agents" in {r[0] for r in cur.fetchall()}


# ── /agent/list ───────────────────────────────────────────────────────


def test_list_empty(client):
    resp = client.get("/agent/list")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_requires_auth_shape(client):
    """返回结构稳定（分页字段齐全 + tags 候选），便于前端直接消费。"""
    body = client.get("/agent/list", params={"page": 1, "page_size": 5}).json()
    assert set(["total", "page", "page_size", "tags", "items"]).issubset(body.keys())
    assert isinstance(body["tags"], list)


def test_list_exposes_configured_tags(client, monkeypatch):
    """tags 来自配置的 expert_tags，前端据此渲染筛选 chips。"""
    cfg = SimpleNamespace(
        expert_tags=[
            SimpleNamespace(tag_id="macro_research", tag_name="宏观投研"),
            SimpleNamespace(tag_id="bond_research", tag_name="债券投研"),
        ]
    )
    monkeypatch.setattr(expert_api, "get_agent_config", lambda: {"config": cfg})

    body = client.get("/agent/list").json()
    assert body["tags"] == [
        {"tag_id": "macro_research", "tag_name": "宏观投研"},
        {"tag_id": "bond_research", "tag_name": "债券投研"},
    ]


def test_tag_options_accepts_dicts_and_models():
    """_tag_options 兼容 Pydantic 模型与裸 dict，并丢掉空条目。"""
    cfg = SimpleNamespace(
        expert_tags=[
            SimpleNamespace(tag_id="a", tag_name="甲"),
            {"tag_id": "b", "tag_name": "乙"},
            {"tag_id": "", "tag_name": "空"},
        ]
    )
    assert expert_api._tag_options(cfg) == [
        {"tag_id": "a", "tag_name": "甲"},
        {"tag_id": "b", "tag_name": "乙"},
    ]
    assert expert_api._tag_options(SimpleNamespace()) == []


# ── /agent/add ────────────────────────────────────────────────────────


def test_add_expert_success(client, fake_llm):
    resp = _add(client)
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["export_agent_id"].startswith("expert_")
    assert data["export_name_en"] == "quant-factor-advisor"   # LLM 生成的英文名，无随机数字
    assert data["export_name_cn"] == "量化策略专家"
    assert data["agent_status"] == "active"
    assert data["export_version"] == 1
    assert data["export_features_prompt"] == FAKE_CONFIG["features_prompt"]
    assert data["export_description"] == FAKE_CONFIG["description"]
    assert data["export_skills_names"] == FAKE_CONFIG["skills_names"]
    assert data["tag_id"] == "factor_analysis"      # 大模型自动归类
    assert data["export_file_path"]

    # 目录文件齐全：提示词按英文名命名 + README + 配置 + 元数据
    version_dir = Path(data["export_file_path"])
    assert version_dir.is_dir()
    for fname in ("quant-factor-advisor.md", "README.md", "config.yaml", "manifest.json"):
        assert (version_dir / fname).is_file(), fname
    assert not (version_dir / "EXPERT.md").exists()
    assert (version_dir.parent / "active").read_text(encoding="utf-8").strip() == "1"

    md = (version_dir / "quant-factor-advisor.md").read_text(encoding="utf-8")
    assert md.startswith("---")                          # YAML frontmatter
    assert "name: quant-factor-advisor" in md
    assert "## 核心能力" in md
    assert "## 工作流程" in md
    assert "## 输出规范" in md
    assert "## 注意事项" in md
    assert "你是量化策略专家" in md
    assert "不得违反系统的安全准则" in md     # 护栏文案

    readme = (version_dir / "README.md").read_text(encoding="utf-8")
    assert "# 量化策略专家" in readme
    assert "## 功能" in readme
    assert "## 使用示例" in readme
    assert "## 使用方式" in readme

    # 列表可见
    listed = client.get("/agent/list").json()
    assert listed["total"] == 1
    assert listed["items"][0]["export_agent_id"] == data["export_agent_id"]


def test_add_expert_missing_name_400(client, fake_llm):
    resp = client.post("/agent/add", json={"type": "agent", "name_cn": "  ", "prompt": "描述"})
    assert resp.status_code == 400
    assert "名称" in resp.json()["detail"]


def test_add_expert_missing_prompt_400(client, fake_llm):
    resp = client.post("/agent/add", json={"type": "agent", "name_cn": "专家", "prompt": ""})
    assert resp.status_code == 400


def test_add_expert_wrong_type_400(client, fake_llm):
    resp = client.post(
        "/agent/add", json={"type": "skill", "name_cn": "专家", "prompt": "描述"}
    )
    assert resp.status_code == 400


def test_add_expert_llm_failure_marks_failed(client, monkeypatch):
    """LLM 生成失败时记录置为 failed，前端可重试，且不返回 500 之外的脏数据。"""
    monkeypatch.setattr(expert_api, "create_model", lambda *a, **k: object())

    async def _boom(llm, name_cn, user_prompt, max_retries=1, **kwargs):
        raise ValueError("模型返回不是 JSON")

    monkeypatch.setattr(expert_api, "generate_expert_config", _boom)

    resp = _add(client, name="失败专家", prompt="描述")
    assert resp.status_code == 500
    assert "模型" in resp.json()["detail"]

    listed = client.get("/agent/list", params={"status": "failed"}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["agent_status"] == "failed"


def test_add_expert_ignores_manual_tag_param(client, monkeypatch):
    """tag_id 不再接收前端传参：即使请求体里带了也会被忽略，只认大模型归类结果。"""
    monkeypatch.setattr(
        expert_api, "create_model", lambda *a, **k: SimpleNamespace(name="fake-llm")
    )

    async def _fake_generate(llm, name_cn, user_prompt, max_retries=1, **kwargs):
        return {**FAKE_CONFIG, "tag_id": ""}

    monkeypatch.setattr(expert_api, "generate_expert_config", _fake_generate)

    resp = client.post(
        "/agent/add",
        json={
            "type": "agent",
            "name_cn": "手工分类专家",
            "prompt": "描述",
            "tag_id": "macro_research",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tag_id"] == ""       # 大模型没归类 → 保持为空，不落手工值


def test_update_expert_refreshes_generated_metadata(client, fake_llm):
    """编辑后卡片/详情页字段随新版本一起刷新。"""
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    updated = client.post(
        "/agent/update",
        json={"export_agent_id": agent_id, "modify_type": "edit"},
    ).json()
    assert updated["export_description"] == FAKE_CONFIG["description"]
    assert updated["export_skills_names"] == FAKE_CONFIG["skills_names"]
    assert updated["tag_id"] == "factor_analysis"


# ── /agent/update ─────────────────────────────────────────────────────


def test_update_expert_creates_new_version(client, fake_llm):
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    resp = client.post(
        "/agent/update",
        json={
            "export_agent_id": agent_id,
            "name_cn": "量化策略专家Pro",
            "prompt": "升级版描述",
            "modify_type": "edit",
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()

    assert updated["export_name_cn"] == "量化策略专家Pro"
    assert updated["export_prompt"] == "升级版描述"
    assert updated["export_version"] == 2

    expert_dir = Path(updated["export_file_path"]).parent
    assert (expert_dir / "v1").is_dir()      # 旧版本保留
    assert (expert_dir / "v2").is_dir()      # 新版本新建
    assert (expert_dir / "active").read_text(encoding="utf-8").strip() == "2"

    # 老版本内容未被覆盖
    v1_md = (expert_dir / "v1" / "quant-factor-advisor.md").read_text(encoding="utf-8")
    assert "你是量化策略专家" in v1_md


def test_update_expert_partial_keeps_old_values(client, fake_llm):
    created = _add(client, name="原名字", prompt="原描述").json()

    resp = client.post(
        "/agent/update", json={"export_agent_id": created["export_agent_id"]}
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["export_name_cn"] == "原名字"
    assert updated["export_prompt"] == "原描述"
    assert updated["export_version"] == 2


def test_update_expert_not_found_404(client, fake_llm):
    resp = client.post(
        "/agent/update", json={"export_agent_id": "not-exist", "name_cn": "x", "prompt": "y"}
    )
    assert resp.status_code == 404


def test_update_expert_invalid_name_400(client, fake_llm):
    created = _add(client).json()
    resp = client.post(
        "/agent/update",
        json={"export_agent_id": created["export_agent_id"], "name_cn": "x" * 51},
    )
    assert resp.status_code == 400


# ── 中文名唯一性（创建 / 修改） ────────────────────────────────────────


def test_add_expert_duplicate_name_409(client, fake_llm):
    """重名直接拦下：返回 409 + 可展示提示，且不再触发 LLM 生成。"""
    assert _add(client, name="量化策略专家").status_code == 200
    assert fake_llm["generate_calls"] == 1

    resp = _add(client, name="量化策略专家", prompt="另一个描述")
    assert resp.status_code == 409, resp.text
    assert "已有同名的专家Agent" in resp.json()["detail"]
    assert fake_llm["generate_calls"] == 1      # 未发生第二次生成

    assert client.get("/agent/list").json()["total"] == 1


def test_add_expert_duplicate_name_ignores_space_and_case(client, fake_llm):
    _add(client, name="Quant Factor 专家")
    resp = _add(client, name="  quant   factor 专家 ")
    assert resp.status_code == 409


def test_add_expert_same_name_as_deleted_ok(client, fake_llm):
    """已删除（软删除）的专家不再占用名字：删掉后可重建同名专家。"""
    created = _add(client, name="待删专家").json()
    client.post("/agent/del", json={"export_agent_id": created["export_agent_id"]})

    resp = _add(client, name="待删专家")
    assert resp.status_code == 200, resp.text
    assert client.get("/agent/list").json()["total"] == 1   # 已删除的不在列表里


def test_add_expert_same_name_in_other_account_ok(client, fake_llm, db):
    """重名只在本用户范围内判定，别人叫这个名字不影响自己创建。"""
    ExpertStore(db).create(
        {
            "export_agent_id": "expert_other_00000001",
            "user_id": "another-user",
            "export_name_en": "quant-factor-advisor",
            "export_name_cn": "量化策略专家",
            "export_prompt": "别人的专家",
            "agent_status": "active",
            "export_version": 1,
        }
    )
    assert _add(client, name="量化策略专家").status_code == 200


def test_update_expert_duplicate_name_409(client, fake_llm, db):
    """改名撞上其他专家：409 + 提示改名字，且版本不推进、生成不触发。"""
    first = _add(client, name="专家甲").json()
    second = _add(client, name="专家乙").json()
    calls_before = fake_llm["generate_calls"]

    resp = client.post(
        "/agent/update",
        json={
            "export_agent_id": first["export_agent_id"],
            "name_cn": "专家乙",
            "prompt": "想改成别人的名字",
            "modify_type": "edit",
        },
    )
    assert resp.status_code == 409, resp.text
    assert "请修改专家名称" in resp.json()["detail"]
    assert fake_llm["generate_calls"] == calls_before

    store = ExpertStore(db)
    unchanged = store.get(first["export_agent_id"])
    assert unchanged["export_name_cn"] == "专家甲"
    assert unchanged["export_version"] == 1
    assert store.get(second["export_agent_id"])["export_name_cn"] == "专家乙"


def test_update_expert_same_name_not_blocked(client, fake_llm):
    """原名保存（与自己同名）不应被误判为重名。"""
    created = _add(client, name="量化策略专家").json()

    resp = client.post(
        "/agent/update",
        json={
            "export_agent_id": created["export_agent_id"],
            "name_cn": "量化策略专家",
            "prompt": "不改名，只改描述",
            "modify_type": "edit",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["export_name_cn"] == "量化策略专家"
    assert resp.json()["export_version"] == 2


def test_update_expert_name_of_deleted_expert_ok(client, fake_llm):
    """改名撞上已删除的专家不算冲突（已删除的不参与重名校验）。"""
    first = _add(client, name="专家甲").json()
    gone = _add(client, name="专家乙").json()
    client.post("/agent/del", json={"export_agent_id": gone["export_agent_id"]})

    resp = client.post(
        "/agent/update",
        json={"export_agent_id": first["export_agent_id"], "name_cn": "专家乙"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["export_name_cn"] == "专家乙"


# ── /agent/del ────────────────────────────────────────────────────────


def test_delete_expert_soft(client, fake_llm):
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    resp = client.post("/agent/del", json={"export_agent_id": agent_id})
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    # 默认列表不再返回
    assert client.get("/agent/list").json()["total"] == 0
    # 显式索要时能看到 disabled
    listed = client.get("/agent/list", params={"include_disabled": True}).json()
    assert listed["total"] == 1
    assert listed["items"][0]["agent_status"] == "disabled"
    # 目录仍在（软删除不删文件）
    assert Path(created["export_file_path"]).is_dir()


def test_delete_expert_hard(client, fake_llm):
    created = _add(client).json()
    agent_id = created["export_agent_id"]
    expert_dir = Path(created["export_file_path"]).parent

    resp = client.post("/agent/del", json={"export_agent_id": agent_id, "hard": True})
    assert resp.status_code == 200
    assert resp.json()["hard"] is True

    assert client.get("/agent/list", params={"include_disabled": True}).json()["total"] == 0
    assert not expert_dir.exists()


def test_delete_expert_not_found_404(client, fake_llm):
    assert client.post("/agent/del", json={"export_agent_id": "nope"}).status_code == 404


# ── /agent/stream_export_run ──────────────────────────────────────────


@pytest.fixture()
def stub_stream(monkeypatch):
    """打桩 Agent 获取与流式生成，捕获传入参数。"""
    captured = {}

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        captured["session_id"] = session_id
        return SimpleNamespace(
            workspace_dir=None, workspace_virtual_path="/workspace", model_name="m"
        )

    async def fake_stream(**kwargs):
        captured["request"] = kwargs["request"]
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(expert_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(expert_api, "chat_stream_generator", fake_stream)
    return captured


@pytest.fixture()
def fake_relevance(monkeypatch):
    """打桩相关性预检：分类结果由用例预设，记录调用与入参。

    只替换 ``check_relevance``（真正调 LLM 的那一步），``should_reject`` /
    ``build_refusal`` / ``build_basis`` 仍用真实实现，保证口径与文案被真实覆盖。
    """
    state = {"classification": "relevant", "calls": 0}

    async def _fake_check(
        llm,
        basis,
        message,
        timeout=0,
        context="",
        disable_thinking=True,
        attachments=None,
    ):
        state["calls"] += 1
        state["basis"] = basis
        state["message"] = message
        state["context"] = context
        state["disable_thinking"] = disable_thinking
        state["attachments"] = attachments or []
        return state["classification"]

    monkeypatch.setattr(
        expert_api,
        "expert_relevance",
        SimpleNamespace(
            check_relevance=_fake_check,
            should_reject=expert_relevance.should_reject,
            build_refusal=expert_relevance.build_refusal,
            build_basis=expert_relevance.build_basis,
            # 高危红线与能力抽取用真实实现：这两条是「宽松口径也要拦住」的保证
            detect_unsafe_request=expert_relevance.detect_unsafe_request,
            extract_capabilities=expert_relevance.extract_capabilities,
            UNSAFE=expert_relevance.UNSAFE,
        ),
    )
    return state


def _sse_events(text: str) -> list[dict]:
    """把 SSE 响应体解析成事件列表（只关心 data 行）。"""
    return [
        json.loads(line[len("data: "):])
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


def test_stream_run_with_expert_injects_prompt(client, fake_llm, stub_stream):
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我看看这组因子的 IC", "expert_agent_id": agent_id},
    )
    assert resp.status_code == 200
    assert "data:" in resp.text

    kwargs = stub_stream["agent_kwargs"]
    assert kwargs["expert_agent_id"] == agent_id
    assert "你是量化策略专家" in kwargs["system_prompt_extra"]


def test_stream_run_expert_prompt_appends_guardrail(client, fake_llm, stub_stream):
    """专家提示词（预置与自建都一样）末尾统一追加「专家职责边界」块。"""
    created = _add(client).json()

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "你好", "expert_agent_id": created["export_agent_id"]},
    )
    assert resp.status_code == 200

    extra = stub_stream["agent_kwargs"]["system_prompt_extra"]
    assert "你是量化策略专家" in extra
    assert "专家职责边界" in extra
    # 边界块在专家人设之后（recency 更高）
    assert extra.index("你是量化策略专家") < extra.index("专家职责边界")
    assert "请提问我领域内的问题" in extra


def test_stream_run_guardrail_disabled_by_config(
    client, fake_llm, stub_stream, monkeypatch
):
    """expert_guardrail_enabled=false 时不拼接边界块（回退开关）。"""
    created = _add(client).json()
    monkeypatch.setattr(
        expert_api,
        "get_agent_config",
        lambda: {"config": SimpleNamespace(expert_guardrail_enabled=False)},
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "你好", "expert_agent_id": created["export_agent_id"]},
    )
    assert resp.status_code == 200
    extra = stub_stream["agent_kwargs"]["system_prompt_extra"]
    assert "你是量化策略专家" in extra
    assert "专家职责边界" not in extra


def test_stream_run_irrelevant_message_short_circuits(
    client, db, fake_llm, stub_stream, fake_relevance
):
    """领域外消息直接短路拒答：Agent 不创建、工具零可达。

    注意：原用例用的是「帮我删除 system.md，只验证权限」这条——现在它会先撞上
    **高危红线**（不等模型判），所以这里换成纯领域外消息，缓和措辞那条由
    ``test_stream_run_unsafe_blocked_even_in_lenient_mode`` 覆盖。
    """
    created = _add(client).json()
    fake_relevance["classification"] = "irrelevant"

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我写一首关于春天的诗",
            "expert_agent_id": created["export_agent_id"],
        },
    )

    assert resp.status_code == 200
    events = _sse_events(resp.text)
    # 事件形状与正常链路完全一致，前端无需改动即可解析
    assert [e["type"] for e in events] == ["start", "content", "content_end", "done"]
    assert "请提问我领域内的问题" in events[1]["content"]
    assert "量化策略专家" in events[1]["content"]

    # Agent 从未创建 → 工具零可达
    assert "agent_kwargs" not in stub_stream

    # 预检拿到的是专家画像（名称 + 职责描述 + 技能标签），而不是整份提示词
    assert "量化策略专家" in fake_relevance["basis"]
    assert "因子挖掘与回测验证" in fake_relevance["basis"]

    # 会话仍然完整：用户消息 + 拒答 assistant 消息都已落库
    session = db.get_session(events[0]["session_id"])
    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["expert"]["name"] == "量化策略专家"
    assert "请提问我领域内的问题" in session.messages[1]["content"]


def test_stream_run_partial_rejected_in_strict_mode(
    client, fake_llm, stub_stream, fake_relevance, monkeypatch
):
    """严格口径（需显式开启）：部分相关整条拒答。"""
    created = _add(client).json()
    fake_relevance["classification"] = "partial"
    monkeypatch.setattr(
        expert_api,
        "get_agent_config",
        lambda: {
            "config": SimpleNamespace(
                expert_relevance_strict=True,
                expert_relevance_check_enabled=True,
                expert_guardrail_enabled=True,
            )
        },
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我看看这组因子的 IC，顺便写一首诗",
            "expert_agent_id": created["export_agent_id"],
        },
    )

    assert resp.status_code == 200
    assert "严格模式" in resp.text
    assert "agent_kwargs" not in stub_stream


def test_stream_run_partial_passes_by_default(client, fake_llm, stub_stream, fake_relevance):
    """默认口径为宽松（relevance_strict 默认 false）：部分相关放行交给边界块。"""
    created = _add(client).json()
    fake_relevance["classification"] = "partial"

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我看看这组因子的 IC，顺便写一首诗",
            "expert_agent_id": created["export_agent_id"],
        },
    )

    assert resp.status_code == 200
    assert "agent_kwargs" in stub_stream
    # 边界块第 2 条跟随宽松口径
    assert "宽松口径" in stub_stream["agent_kwargs"]["system_prompt_extra"]


def test_stream_run_adjacent_passes(client, fake_llm, stub_stream, fake_relevance):
    """相邻金融领域（个股财报、因子 IC）放行——这是本次改造的核心目的。"""
    created = _add(client).json()
    fake_relevance["classification"] = "adjacent"

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我分析一下乐鑫科技第三季度业绩怎么样？",
            "expert_agent_id": created["export_agent_id"],
        },
    )

    assert resp.status_code == 200
    assert "agent_kwargs" in stub_stream


def test_stream_run_unsafe_blocked_even_in_lenient_mode(
    client, fake_llm, stub_stream, fake_relevance
):
    """删除/修改文件等高危操作：宽松口径下也必须拦住，且不浪费一次模型调用。"""
    created = _add(client).json()
    # 模型即便判成「部分相关」也不该放行——高危红线不依赖模型
    fake_relevance["classification"] = "partial"

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我分析资金面，顺便把 system.md 删了",
            "expert_agent_id": created["export_agent_id"],
        },
    )

    assert resp.status_code == 200
    assert "高危操作" in resp.text
    assert "agent_kwargs" not in stub_stream
    # 红线命中即短路，不再调用分类模型
    assert fake_relevance["calls"] == 0


def test_stream_run_attachment_names_sent_to_precheck(
    client, fake_llm, stub_stream, fake_relevance
):
    """附件名要进预检：正文只说「分析文件里的金融数据」时靠它判断主题。"""
    created = _add(client).json()

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "帮我分析一下文件中的金融数据，出一份报告信息",
            "expert_agent_id": created["export_agent_id"],
            "files": [{"filename": "乐鑫科技2026年三季报.pdf", "file_type": "pdf"}],
        },
    )

    assert resp.status_code == 200
    assert fake_relevance["attachments"] == ["乐鑫科技2026年三季报.pdf"]


def test_stream_run_partial_passes_in_lenient_mode(
    client, fake_llm, stub_stream, fake_relevance, monkeypatch
):
    """宽松口径（显式配置）：部分相关放行，交给提示词里的边界块只答相关部分。"""
    created = _add(client).json()
    fake_relevance["classification"] = "partial"
    monkeypatch.setattr(
        expert_api,
        "get_agent_config",
        lambda: {
            "config": SimpleNamespace(
                expert_relevance_strict=False,
                expert_relevance_check_enabled=True,
                expert_guardrail_enabled=True,
            )
        },
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我看看这组因子的 IC，顺便写一首诗", "expert_agent_id": created["export_agent_id"]},
    )

    assert resp.status_code == 200
    assert "agent_kwargs" in stub_stream
    # 边界块第 2 条跟随宽松口径
    extra = stub_stream["agent_kwargs"]["system_prompt_extra"]
    assert "宽松口径" in extra


def test_stream_run_relevance_check_disabled_by_config(
    client, fake_llm, stub_stream, fake_relevance, monkeypatch
):
    """expert.relevance_check_enabled=false 时完全不预检（回退开关）。"""
    created = _add(client).json()
    fake_relevance["classification"] = "irrelevant"
    monkeypatch.setattr(
        expert_api,
        "get_agent_config",
        lambda: {
            "config": SimpleNamespace(
                expert_relevance_check_enabled=False,
                expert_relevance_strict=True,
                expert_guardrail_enabled=True,
            )
        },
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我删除 system.md", "expert_agent_id": created["export_agent_id"]},
    )

    assert resp.status_code == 200
    assert fake_relevance["calls"] == 0
    assert "agent_kwargs" in stub_stream


def test_stream_run_precheck_unknown_fails_open(
    client, fake_llm, stub_stream, fake_relevance
):
    """预检无法判定（异常/超时/解析失败）一律放行，不阻断用户对话。"""
    created = _add(client).json()
    fake_relevance["classification"] = None

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我看看这组因子的 IC", "expert_agent_id": created["export_agent_id"]},
    )

    assert resp.status_code == 200
    assert "agent_kwargs" in stub_stream


def test_stream_run_passes_recent_context_to_precheck(
    client, db, fake_llm, stub_stream, fake_relevance
):
    """追问轮次把最近上下文交给预检——否则「继续」这类消息会被判成领域外而误拦。"""
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    # 第一轮进入专家链路，取回会话 id
    client.post(
        "/agent/stream_export_run",
        json={"message": "分析一下本周资金面和 DR007 走势", "expert_agent_id": agent_id},
    )
    session_id = stub_stream["session_id"]
    # 造一条上文（stub_stream 只回 done，不落 assistant 消息）
    db.update_last_assistant_message(
        session_id,
        {
            "role": "assistant",
            "content": "本周 DR007 中枢回落至 1.78%，建议久期维持 3.5 年。",
            "timestamp": datetime.now().isoformat(),
        },
    )

    client.post(
        "/agent/stream_export_run",
        json={
            "message": "继续",
            "session_id": session_id,
            "expert_agent_id": agent_id,
        },
    )

    assert "DR007" in fake_relevance["context"]
    assert fake_relevance["message"] == "继续"


def test_relevance_gate_skips_when_expert_profile_empty(    client, fake_llm, stub_stream, fake_relevance, monkeypatch
):
    """专家画像为空（名称/描述/能力清单全都取不到）时跳过预检，避免用空画像误判。"""
    created = _add(client).json()
    fake_relevance["classification"] = "irrelevant"
    monkeypatch.setattr(expert_api, "_expert_profile", lambda *a, **k: {})
    # 提示词里能抽到能力清单时仍算「有画像」——这里一并置空才是真正的空画像
    monkeypatch.setattr(
        expert_api.expert_relevance, "extract_capabilities", lambda *a, **k: []
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我写一首关于春天的诗", "expert_agent_id": created["export_agent_id"]},
    )

    assert resp.status_code == 200
    assert fake_relevance["calls"] == 0
    assert "agent_kwargs" in stub_stream


def test_stream_run_without_expert_uses_generic(client, fake_llm, stub_stream, fake_relevance):
    resp = client.post("/agent/stream_export_run", json={"message": "你好"})
    assert resp.status_code == 200
    kwargs = stub_stream["agent_kwargs"]
    # 不传专家时等价于通用 Agent：expert_agent_id 归一化为 ""
    assert kwargs["expert_agent_id"] == ""
    assert "你是量化策略专家" not in kwargs["system_prompt_extra"]
    # 没有专家就没有领域可判，预检不该被调用（省一次 LLM 调用）
    assert fake_relevance["calls"] == 0


def test_stream_run_uses_structured_skills_field(
    client, db, fake_llm, stub_stream, monkeypatch
):
    """专家链路与 chat 一致：`/技能目录名` 由请求体 skills 字段驱动，落库正文保持用户原话。"""
    monkeypatch.setattr(
        skill_library,
        "list_user_skill_dirs",
        lambda username: [{"dir_name": "docx", "path": "/tmp/skills/docx", "enabled": True}],
    )
    monkeypatch.setattr(skill_library, "skill_metadata", lambda path: {"name": "Word 文档"})

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "帮我排个版", "skills": ["docx"]},
    )

    assert resp.status_code == 200
    session = db.get_session(stub_stream["session_id"])
    user_message = session.messages[0]
    assert user_message["content"] == "帮我排个版"
    assert user_message["skills"] == ["docx"]
    assert session.title == "帮我排个版"


def test_stream_run_skills_only_without_text_uses_display_name(
    client, db, fake_llm, stub_stream, monkeypatch
):
    """只挂技能、没写正文：正文允许为空，标题用技能展示名兜底。"""
    monkeypatch.setattr(
        skill_library,
        "list_user_skill_dirs",
        lambda username: [{"dir_name": "docx", "path": "/tmp/skills/docx", "enabled": True}],
    )
    monkeypatch.setattr(skill_library, "skill_metadata", lambda path: {"name": "Word 文档"})

    resp = client.post(
        "/agent/stream_export_run", json={"message": "", "skills": ["docx"]}
    )

    assert resp.status_code == 200
    session = db.get_session(stub_stream["session_id"])
    assert session.title == "Word 文档"
    assert session.messages[0]["skills"] == ["docx"]


def test_stream_run_unknown_expert_404(client, fake_llm, stub_stream):
    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "hi", "expert_agent_id": "not-exist"},
    )
    assert resp.status_code == 404


def test_stream_run_disabled_expert_400(client, fake_llm, stub_stream):
    created = _add(client).json()
    agent_id = created["export_agent_id"]
    client.post("/agent/del", json={"export_agent_id": agent_id})

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "hi", "expert_agent_id": agent_id},
    )
    assert resp.status_code == 400
    assert "停用" in resp.json()["detail"]


def test_stream_run_missing_expert_dir_degrades_to_generic(
    client, fake_llm, stub_stream
):
    """磁盘目录被清掉时降级为通用 Agent，不阻断用户对话。"""
    created = _add(client).json()
    agent_id = created["export_agent_id"]

    import shutil

    shutil.rmtree(Path(created["export_file_path"]).parent, ignore_errors=True)

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "hi", "expert_agent_id": agent_id},
    )
    assert resp.status_code == 200
    kwargs = stub_stream["agent_kwargs"]
    assert kwargs["expert_agent_id"] == ""            # 降级
    assert "你是量化策略专家" not in kwargs["system_prompt_extra"]


def test_stream_run_uses_existing_session(client, fake_llm, stub_stream):
    """带 session_id 时复用会话，不再新建。"""
    created = _add(client).json()
    session_resp = client.post("/agent/sessions", json={"title": "测试会话"})
    session_id = session_resp.json()["session_id"]

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "hi",
            "session_id": session_id,
            "expert_agent_id": created["export_agent_id"],
        },
    )
    assert resp.status_code == 200
    assert stub_stream["session_id"] == session_id


def test_stream_run_other_users_session_404(client, fake_llm, stub_stream, db):
    created = _add(client).json()
    # 直接在库里造一条属于别人的会话
    from easy_agent.models.db import SessionModel

    db.create_session(
        SessionModel(
            session_id="other-session",
            title="别人的",
            messages=[],
            created_at="t",
            updated_at="t",
            username="someone-else",
            workspace_name="ws",
        )
    )

    resp = client.post(
        "/agent/stream_export_run",
        json={
            "message": "hi",
            "session_id": "other-session",
            "expert_agent_id": created["export_agent_id"],
        },
    )
    assert resp.status_code == 404


def test_stream_run_empty_message_400(client):
    """正文允许为空（只挂技能/连接器），但三者全空 → 400。缺 message 字段仍 422。"""
    assert client.post("/agent/stream_export_run", json={"message": ""}).status_code == 400
    assert client.post("/agent/stream_export_run", json={}).status_code == 422


# ── 端到端辅助：真实读回磁盘提示词 ────────────────────────────────────


def test_load_active_prompt_after_add(client, fake_llm):
    created = _add(client).json()
    text, version, version_dir = load_active_prompt("testuser", created["export_agent_id"])
    assert version == 1
    assert "你是量化策略专家" in text
    assert Path(version_dir).is_dir()


# ── HITL 恢复（/agent/chat/resume）必须保留专家 ──────────────────────────
#
# 回归背景：/agent/chat/resume 若不带会话专家 ID 去调 get_or_create_agent_for_session()，
# 会被缓存驱逐逻辑判定为「专家 -> 通用」的变更，从而丢掉缓存、重建出没有人设的通用
# Agent（checkpointer 的中断态虽在，但专家人设消失）。这里锁住该行为不退化。


@pytest.fixture()
def stub_resume(monkeypatch):
    """打桩 resume 路径上的 Agent 获取与流式生成，捕获传入参数。"""
    captured = {}

    async def fake_get_agent(session_id, username, workspace_name, **kwargs):
        captured["agent_kwargs"] = kwargs
        return SimpleNamespace(
            workspace_dir=None, workspace_virtual_path="/workspace", model_name="m"
        )

    async def fake_resume(**kwargs):
        yield 'data: {"type": "done"}\n\n'

    monkeypatch.setattr(chat_api, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_api, "resume_stream_generator", fake_resume)
    return captured


def _make_session(db, session_id, username="testuser", workspace_name="ws-hitl"):
    db.create_session(
        SessionModel(
            session_id=session_id,
            title="t",
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
            username=username,
            workspace_name=workspace_name,
        )
    )


def _resume(client, session_id, message_id=None):
    return client.post(
        "/agent/chat/resume",
        json={
            "session_id": session_id,
            "thread_id": f"{session_id}-m1",
            "decisions": [{"type": "approve"}],
            "message_id": message_id,
        },
    )


def test_resume_keeps_session_expert(client, db, stub_resume, monkeypatch):
    """专家会话里触发 HITL 并恢复：仍以该专家身份复用/重建 Agent。"""
    sid = "sess-hitl-1"
    _make_session(db, sid)
    monkeypatch.setitem(am._session_expert, sid, "expert_quant")

    resp = _resume(client, sid)
    assert resp.status_code == 200

    # 关键断言：带上专家 ID -> 命中缓存原样复用，不会误判为「专家变更」而驱逐
    assert stub_resume["agent_kwargs"]["expert_agent_id"] == "expert_quant"


def test_resume_generic_session_passes_empty_expert(client, db, stub_resume):
    """通用会话恢复：专家 ID 为空串，行为与引入专家模块前逐字一致。"""
    sid = "sess-hitl-2"
    _make_session(db, sid)

    resp = _resume(client, sid)
    assert resp.status_code == 200
    assert stub_resume["agent_kwargs"]["expert_agent_id"] == ""


def test_resume_unknown_session_404(client, stub_resume):
    assert _resume(client, "no-such-session").status_code == 404


# ── 公共（预置）专家：列表 + 召唤 ──────────────────────────────────────

PUBLIC_PROMPT = "你是「预置演示专家」，只回答宏观策略相关问题。"
PUBLIC_NAME = "预置演示专家"


@pytest.fixture()
def public_root(tmp_path, monkeypatch):
    """把公共专家目录指向临时目录，不依赖仓库自带的 experts/。

    应用启动时已加载 agent 配置（tools.experts_dir 指向仓库 experts/），故这里
    直接替换 ``public_expert_service.public_experts_root``，保证用例只看到自造数据。
    """
    from easy_agent.services import public_expert_service

    root = tmp_path / "experts"
    root.mkdir()
    monkeypatch.setattr(public_expert_service, "public_experts_root", lambda: root)
    return root


def _write_public_expert(
    root,
    expert_id="preset_demo",
    *,
    name_cn=PUBLIC_NAME,
    prompt=PUBLIC_PROMPT,
    tag_id="macro_research",
):
    """写一个平铺布局的公共专家目录（config.yaml + 提示词 md）。"""
    expert_dir = root / expert_id
    expert_dir.mkdir(parents=True, exist_ok=True)
    (expert_dir / "config.yaml").write_text(
        "\n".join(
            [
                f"expert_agent_id: {expert_id}",
                f"name_cn: {name_cn}",
                "name_en: preset-demo",
                "version: 1",
                f"tag_id: {tag_id}",
                "description: 预置演示专家的功能描述",
                "skills_names: [宏观投研, 利率研判]",
                "features_prompt: [帮我解读最新的社融数据]",
            ]
        ),
        encoding="utf-8",
    )
    (expert_dir / "preset-demo.md").write_text(prompt, encoding="utf-8")
    return expert_dir


def test_public_list_returns_presets(client, public_root):
    _write_public_expert(public_root)
    resp = client.get("/agent/public-list")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["export_agent_id"] == "preset_demo"
    assert item["export_name_cn"] == PUBLIC_NAME
    assert item["tag_id"] == "macro_research"
    assert item["export_skills_names"] == ["宏观投研", "利率研判"]
    assert item["agent_status"] == "active"
    # 只读标记：前端据此隐藏「编辑 / 删除」
    assert item["scope"] == "public"
    # 与 /agent/list 的响应同构（tags 供 chips 复用）
    assert isinstance(data["tags"], list)


def test_public_list_empty_when_dir_missing(client, tmp_path, monkeypatch):
    from easy_agent.services import public_expert_service

    monkeypatch.setattr(
        public_expert_service, "public_experts_root", lambda: tmp_path / "nope"
    )
    resp = client.get("/agent/public-list")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


def test_stream_run_with_public_expert_injects_prompt(client, public_root, stub_stream):
    """召唤公共专家：不查库也能把提示词注入系统提示词。"""
    _write_public_expert(public_root)

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "解读一下最新的社融数据", "expert_agent_id": "preset_demo"},
    )
    assert resp.status_code == 200
    assert "data:" in resp.text

    kwargs = stub_stream["agent_kwargs"]
    assert kwargs["expert_agent_id"] == "preset_demo"
    assert PUBLIC_PROMPT in kwargs["system_prompt_extra"]


def test_stream_run_public_expert_without_prompt_404(client, public_root, stub_stream):
    """公共专家的提示词文件缺失 = 该预置专家不存在（列表不展示、召唤 404）。

    与「我的专家」的策略不同：那边有数据库记录兜底，磁盘缺失时降级为通用 Agent；
    公共专家的存在标志就是提示词文件本身，缺失属打包错误，必须显式暴露而不是
    静默地用通用 Agent 回答（否则用户以为召唤成功了）。
    """
    expert_dir = _write_public_expert(public_root)
    (expert_dir / "preset-demo.md").unlink()

    # 列表里不再出现
    assert client.get("/agent/public-list").json()["items"] == []

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "hi", "expert_agent_id": "preset_demo"},
    )
    assert resp.status_code == 404


def test_my_expert_wins_over_same_id_in_public_dir(
    client, fake_llm, public_root, stub_stream
):
    """同一个 ID 同时命中我的专家与公共专家时，以「我的专家」为准。"""
    agent_id = _add(client).json()["export_agent_id"]
    _write_public_expert(public_root, expert_id=agent_id)

    resp = client.post(
        "/agent/stream_export_run",
        json={"message": "hi", "expert_agent_id": agent_id},
    )
    assert resp.status_code == 200
    injected = stub_stream["agent_kwargs"]["system_prompt_extra"]
    assert "你是量化策略专家" in injected
    assert PUBLIC_PROMPT not in injected
