"""批次 2B：``api/expert.py`` 辅助逻辑拆分到 ``api/expert_helpers.py`` 的回归。

覆盖：

- 旧内部名字仍从 ``easy_agent.api.expert`` 可用，且与 helpers 是**同一对象**；
- helpers 不反向 import ``api.expert``（依赖方向单向，避免循环）；
- 关键纯函数 / 轻量装配的行为样本：``_to_public``、``_as_str_list``、
  ``_tag_options``、``_expert_flags``、``_with_guardrail``、
  ``_ensure_name_available``、``_expert_meta_for_message``、``_persist_user_turn``；
- 专家路由仍挂在原 URL/方法/标签上（OpenAPI 路径与方法不变）。
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import easy_agent.api.expert as expert_api
import easy_agent.api.expert_helpers as expert_helpers
import easy_agent.services.expert_runtime as expert_runtime
from easy_agent.app import app

# 批次 2B 纯移动的符号清单（实现位于 helpers，expert 仅 re-export）
MOVED_HELPERS = [
    "_as_str_list",
    "_current_user_id",
    "_ensure_name_available",
    "_expert_flags",
    "_expert_meta_for_message",
    "_expert_profile",
    "_expert_tag_name",
    "_generate_and_store",
    "_get_config",
    "_load_expert_prompt",
    "_persist_user_turn",
    "_precheck_disable_thinking",
    "_precheck_llm",
    "_recent_context",
    "_refusal_event_stream",
    "_relevance_gate",
    "_session_logger_for",
    "_tag_options",
    "_to_public",
    "_with_guardrail",
]

EXPECTED_ROUTES = {
    ("POST", "/agent/add"),
    ("POST", "/agent/update"),
    ("GET", "/agent/list"),
    ("GET", "/agent/public-list"),
    ("POST", "/agent/del"),
    ("GET", "/agent/expert/required-skills"),
}


# ── re-export / 依赖方向 ──────────────────────────────────────────────


def test_helpers_are_reexported_by_identity():
    """旧导入路径（``expert_api._to_public`` 等）必须是同一对象，而非拷贝。"""
    assert set(MOVED_HELPERS) <= set(expert_api.__all__)
    for name in MOVED_HELPERS:
        assert getattr(expert_api, name) is getattr(expert_helpers, name), name


def test_helpers_do_not_import_api_expert():
    """helpers 不 import api.expert（否则形成 api.expert ↔ api.expert_helpers 环）。"""
    source = Path(expert_helpers.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 1 and module in ("", "expert") and any(
                alias.name == "expert" for alias in node.names
            ):
                forbidden.append(node)
            if module == "easy_agent.api.expert":
                forbidden.append(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "easy_agent.api.expert" or alias.name.startswith(
                    "easy_agent.api.expert."
                ):
                    forbidden.append(node)
    assert forbidden == []


# ── 纯函数样本 ────────────────────────────────────────────────────────


def test_to_public_projects_only_public_fields():
    record = {
        "export_agent_id": "expert_1",
        "session_id": "sess_1",
        "tag_id": "bond_research",
        "export_name_en": "bond-advisor",
        "export_name_cn": "债券投研专家",
        "export_prompt": "用户描述",
        "export_description": "面向债券投研场景",
        "export_skills_names": ["久期分析", "信用研究"],
        "export_features_prompt": ["帮我看这周资金面"],
        "export_file_path": "/tmp/expert_1/v1",
        "agent_status": "active",
        "export_version": 2,
        "create_timestamp": "t1",
        "update_timestamp": "t2",
        # 以下内部列不应对外暴露
        "user_id": "u1",
        "disabled": 1,
    }
    assert expert_helpers._to_public(record) == {
        "export_agent_id": "expert_1",
        "session_id": "sess_1",
        "tag_id": "bond_research",
        "export_name_en": "bond-advisor",
        "export_name_cn": "债券投研专家",
        "export_prompt": "用户描述",
        "export_description": "面向债券投研场景",
        "export_skills_names": ["久期分析", "信用研究"],
        "export_features_prompt": ["帮我看这周资金面"],
        "export_file_path": "/tmp/expert_1/v1",
        "agent_status": "active",
        "export_version": 2,
        "create_timestamp": "t1",
        "update_timestamp": "t2",
    }
    assert expert_helpers._to_public(None) == {}
    assert expert_helpers._to_public({}) == {}
    # 缺列时回退默认值（列表 -> []、版本 -> 1）
    partial = expert_helpers._to_public({"export_name_cn": "甲"})
    assert partial["export_skills_names"] == []
    assert partial["export_version"] == 1


def test_as_str_list_normalizes_json_scalars_and_garbage():
    assert expert_helpers._as_str_list(None) == []
    assert expert_helpers._as_str_list("") == []
    assert expert_helpers._as_str_list("   ") == []
    assert expert_helpers._as_str_list('["久期", " 信用 ", ""]') == ["久期", "信用"]
    assert expert_helpers._as_str_list(["a", "", " b "]) == ["a", "b"]
    # 非 JSON 的裸字符串按单元素处理；JSON 但非数组（dict/数字）返回 []
    assert expert_helpers._as_str_list("纯文本") == ["纯文本"]
    assert expert_helpers._as_str_list('{"a": 1}') == []
    assert expert_helpers._as_str_list(123) == []


def test_tag_options_accepts_models_and_dicts_and_drops_empty():
    cfg = SimpleNamespace(
        expert_tags=[
            SimpleNamespace(tag_id="macro_research", tag_name="宏观投研"),
            {"tag_id": "bond_research", "tag_name": " 债券投研 "},
            {"tag_id": "", "tag_name": "空 ID"},
            {"tag_id": "no_name", "tag_name": ""},
        ]
    )
    assert expert_helpers._tag_options(cfg) == [
        {"tag_id": "macro_research", "tag_name": "宏观投研"},
        {"tag_id": "bond_research", "tag_name": "债券投研"},
    ]
    assert expert_helpers._tag_options(SimpleNamespace()) == []


def test_expert_flags_defaults_on_missing_or_broken_config(monkeypatch):
    # 2026-10 迁移后 _expert_flags 落在 services.expert_runtime（expert_helpers 仅 re-export），
    # 打桩必须指向真实定义处。
    monkeypatch.setattr(expert_runtime, "get_agent_config", lambda: {})
    assert expert_runtime._expert_flags() == (True, True, False)

    cfg = SimpleNamespace(
        expert_guardrail_enabled=False,
        expert_relevance_check_enabled=False,
        expert_relevance_strict=True,
    )
    monkeypatch.setattr(expert_runtime, "get_agent_config", lambda: {"config": cfg})
    assert expert_runtime._expert_flags() == (False, False, True)

    def _boom():
        raise RuntimeError("配置未初始化")

    monkeypatch.setattr(expert_runtime, "get_agent_config", _boom)
    assert expert_runtime._expert_flags() == (True, True, False)


def test_with_guardrail_wires_config_flags(monkeypatch):
    monkeypatch.setattr(
        expert_runtime,
        "get_agent_config",
        lambda: {
            "config": SimpleNamespace(
                expert_guardrail_enabled=True, expert_relevance_strict=False
            )
        },
    )
    guarded = expert_runtime._with_guardrail("你是债券投研专家")
    assert "你是债券投研专家" in guarded
    assert "专家职责边界" in guarded
    assert "宽松口径" in guarded

    monkeypatch.setattr(
        expert_runtime,
        "get_agent_config",
        lambda: {"config": SimpleNamespace(expert_guardrail_enabled=False)},
    )
    assert expert_runtime._with_guardrail("你是债券投研专家") == "你是债券投研专家"
    assert expert_runtime._with_guardrail("") == ""


class _NameStore:
    """只实现 ``_ensure_name_available`` 用到的查询，避免依赖真实 DB。"""

    def __init__(self, conflict):
        self.conflict = conflict
        self.calls: list[tuple[str, str, str]] = []

    def find_by_name_cn(self, user_id, name_cn, exclude_agent_id=""):
        self.calls.append((user_id, name_cn, exclude_agent_id))
        return self.conflict


def test_ensure_name_available_raises_409_only_on_conflict():
    with pytest.raises(HTTPException) as exc_info:
        expert_helpers._ensure_name_available(_NameStore({"id": "x"}), "u1", "同名专家")
    assert exc_info.value.status_code == 409
    assert "已有同名" in exc_info.value.detail

    store = _NameStore(None)
    assert expert_helpers._ensure_name_available(store, "u1", "新专家") is None
    assert store.calls == [("u1", "新专家", "")]


class _ExpertRepo:
    """``_expert_meta_for_message`` / ``_persist_user_turn`` 的最小假仓库。"""

    def __init__(self, owned=None):
        self.owned = owned
        self.added: list[tuple[str, dict]] = []

    def get_user_by_username(self, username):
        return None

    def get_owned(self, expert_agent_id, user_id):
        return self.owned

    def add_message(self, session_id, message):
        self.added.append((session_id, message))


def test_expert_meta_for_message_prefers_owned_and_skips_unknown():
    repo = _ExpertRepo({"export_name_cn": "债券投研专家"})
    assert expert_helpers._expert_meta_for_message(repo, "u", "expert_1") == {
        "id": "expert_1",
        "name": "债券投研专家",
    }
    # 没有专家 / 查不到时返回 None（调用方不写该字段，不阻断对话）
    assert expert_helpers._expert_meta_for_message(repo, "u", "") is None
    assert expert_helpers._expert_meta_for_message(_ExpertRepo(), "u", "missing") is None


def test_persist_user_turn_keeps_expert_files_and_skill_dirs():
    repo = _ExpertRepo({"export_name_cn": "债券投研专家"})
    request = SimpleNamespace(
        message="看看这周资金面", files=[{"filename": "资金面.pdf"}]
    )
    message = expert_helpers._persist_user_turn(
        repo, "sess_1", "u1", request, "expert_1", ["docx"]
    )
    assert repo.added == [("sess_1", message)]
    assert message["role"] == "user"
    assert message["content"] == "看看这周资金面"
    assert message["expert"] == {"id": "expert_1", "name": "债券投研专家"}
    assert message["files"] == [{"filename": "资金面.pdf"}]
    assert message["skills"] == ["docx"]
    assert "turn_id" not in message


def test_persist_user_turn_records_turn_id():
    """DA-03：专家链路的用户消息同样带上本轮 id。"""
    repo = _ExpertRepo({"export_name_cn": "债券投研专家"})
    request = SimpleNamespace(message="看看这周资金面", files=None)
    message = expert_helpers._persist_user_turn(
        repo, "sess_1", "u1", request, "expert_1", [], turn_id="m-42"
    )
    assert message["turn_id"] == "m-42"


# ── 路由契约 ──────────────────────────────────────────────────────────


def test_router_keeps_original_paths_methods_and_tags():
    actual = {
        (method, route.path)
        for route in expert_api.router.routes
        for method in route.methods - {"HEAD"}
    }
    assert actual == EXPECTED_ROUTES
    assert expert_api.router.tags == ["Expert"]


def test_routes_mounted_once_and_openapi_paths_unchanged():
    """URL/方法不变；每个 URL 只挂载一次，OpenAPI tag 仍为 Expert。"""
    mounted = [route.path for route in app.routes if route.path in {p for _, p in EXPECTED_ROUTES}]
    assert sorted(mounted) == sorted({p for _, p in EXPECTED_ROUTES})

    schema = app.openapi()
    for method, path in EXPECTED_ROUTES:
        assert path in schema["paths"], path
        operation = schema["paths"][path][method.lower()]
        assert operation["tags"] == ["Expert"]
