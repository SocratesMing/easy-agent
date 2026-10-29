"""DeepAgents 框架适配契约测试。

本仓库有 3 处对 deepagents/langchain 内部实现的适配（execute 描述注入、
SummarizationMiddleware 阈值、MemorySaver 持久化）。升级框架前必须让本测试
失败并逐项复核，避免“升级后静默降级/启动期 TypeError”。

execute 描述注入（FW-04）已从 monkeypatch 迁移到官方 beta API
``HarnessProfile.tool_description_overrides``。因此这里同时验证两件事：

1. profile 注册/查找/中间件钩子仍然存在（API 契约）；
2. 通过 ``create_deep_agent`` 编出的真实 ``FilesystemMiddleware`` 实例上，
   ``execute`` 工具描述确实被替换——只断言“注册函数被调用”无法发现
   profile 查找键或中间件钩子失效。

对应审计项：FW-01（版本漂移）、FW-04/FW-05（私有 API 依赖）。
"""

import importlib.metadata as md
import inspect
from types import SimpleNamespace

from deepagents import HarnessProfile, create_deep_agent, register_harness_profile
from langchain_openai import ChatOpenAI

from easy_agent.agent.hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    install_execute_tool_description,
    register_execute_description_profiles,
)

# 已验证的兼容窗口：升级时先改这里，再跑全量测试与 PoC。
VERIFIED_DEEPAGENTS_MIN = (0, 7, 28)
VERIFIED_DEEPAGENTS_MAX_MAJOR_MINOR = (0, 7)
VERIFIED_LANGCHAIN_MIN = (1, 4, 6)
VERIFIED_LANGCHAIN_MAX_MAJOR_MINOR = (1, 4)

# profile 测试用的唯一模型名：避免与其他用例/真实配置互相污染全局注册表。
_PROFILE_MODEL_ID = "fw04-contract-profile-model"
_CONFIG_MODEL_ID = "fw04-contract-config-model"
_DEPRECATED_MODEL_ID = "fw04-contract-deprecated-model"


def _version_tuple(pkg: str) -> tuple[int, ...]:
    parts = []
    for token in md.version(pkg).split(".")[:3]:
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _dummy_model(model_id: str) -> ChatOpenAI:
    """真实 ChatOpenAI 实例（不会发请求）；provider 解析为 ``openai``。"""
    return ChatOpenAI(
        model=model_id,
        api_key="sk-fw04-contract-not-real",
        base_url="http://127.0.0.1:1",
    )


def _fake_config(model_id: str, *, protocol: str = "openai") -> SimpleNamespace:
    return SimpleNamespace(
        models={"contract": SimpleNamespace(protocol=protocol, model=model_id)},
        llm=SimpleNamespace(protocol=protocol, model=model_id),
    )


def _execute_tool(graph):
    """从编译图取真实 ``ToolNode`` 里的 execute 工具（profile 的最终注入点）。"""
    tools_node = graph.nodes.get("tools")
    assert tools_node is not None, "编译图缺少 tools 节点，HarnessProfile 契约已改变"
    tool_node = getattr(tools_node, "bound", None)
    tools_by_name = getattr(tool_node, "tools_by_name", None)
    assert tools_by_name, "tools 节点不再是带 tools_by_name 的 ToolNode"
    assert "execute" in tools_by_name, "ToolNode 中缺少 execute 工具"
    return tools_by_name["execute"]


def test_deepagents_within_verified_window():
    version = _version_tuple("deepagents")
    assert version >= VERIFIED_DEEPAGENTS_MIN, f"deepagents {version} 过低，请先复核适配"
    assert version[:2] == VERIFIED_DEEPAGENTS_MAX_MAJOR_MINOR, (
        f"deepagents {version} 超出已验证窗口 0.6.x（见 docs/framework-integration.md）。"
        "升级前请复核 write_todos/FilesystemMiddleware/SummarizationMiddleware 适配。"
    )


def test_langchain_within_verified_window():
    version = _version_tuple("langchain")
    assert version >= VERIFIED_LANGCHAIN_MIN, f"langchain {version} 过低"
    assert version[:2] == VERIFIED_LANGCHAIN_MAX_MAJOR_MINOR, (
        f"langchain {version} 超出已验证窗口 1.3.x"
    )


def test_create_deep_agent_public_params_still_exist():
    from deepagents import create_deep_agent as create

    params = inspect.signature(create).parameters
    for name in (
        "model",
        "tools",
        "system_prompt",
        "middleware",
        "subagents",
        "skills",
        "memory",
        "permissions",
        "backend",
        "interrupt_on",
        "checkpointer",
        "store",
        "name",
    ):
        assert name in params, f"create_deep_agent 缺少公开参数 {name}（适配失效）"


def test_harness_profile_api_still_exists():
    """官方 beta API：字段/注册函数/顶层导出都必须保持。"""
    from deepagents import profiles as profiles_module

    assert profiles_module.HarnessProfile is HarnessProfile
    assert callable(register_harness_profile)
    fields = HarnessProfile.__dataclass_fields__
    assert "tool_description_overrides" in fields, (
        "HarnessProfile 不再接受 tool_description_overrides，"
        "hitl.register_execute_description_profiles 会静默失效"
    )


def test_filesystem_middleware_description_hook_still_exists():
    """HarnessProfile 的 overrides 最终由 FilesystemMiddleware 消费。"""
    from deepagents.middleware.filesystem import FilesystemMiddleware

    params = inspect.signature(FilesystemMiddleware.__init__).parameters
    assert "custom_tool_descriptions" in params, (
        "FilesystemMiddleware 不再接受 custom_tool_descriptions；"
        "HarnessProfile.tool_description_overrides 将无法落到 execute 工具描述"
    )


def test_harness_profile_applies_execute_description_to_real_middleware():
    """端到端：注册 profile → create_deep_agent → 真实中间件实例描述被替换。"""
    expected = "FW-04 CONTRACT: execute description override"
    register_harness_profile(
        f"openai:{_PROFILE_MODEL_ID}",
        HarnessProfile(tool_description_overrides={"execute": expected}),
    )

    graph = create_deep_agent(model=_dummy_model(_PROFILE_MODEL_ID))

    assert _execute_tool(graph).description == expected, (
        "HarnessProfile.tool_description_overrides 未应用到真实 FilesystemMiddleware"
    )


def test_register_execute_description_profiles_reaches_real_middleware():
    """生产入口：按配置注册的 profile 也必须在真实中间件上生效。"""
    config = _fake_config(_CONFIG_MODEL_ID)

    keys = register_execute_description_profiles(config)

    assert f"openai:{_CONFIG_MODEL_ID}" in keys
    graph = create_deep_agent(model=_dummy_model(_CONFIG_MODEL_ID))
    assert _execute_tool(graph).description == EXECUTE_HITL_TOOL_DESCRIPTION


def test_install_execute_tool_description_no_longer_monkeypatches():
    """FW-04：旧入口是兼容包装，不允许再改写框架类。"""
    from deepagents.middleware.filesystem import FilesystemMiddleware

    before = FilesystemMiddleware.__init__

    assert install_execute_tool_description(_fake_config(_DEPRECATED_MODEL_ID)) is True

    assert FilesystemMiddleware.__init__ is before, (
        "install_execute_tool_description 仍在 monkeypatch FilesystemMiddleware.__init__"
    )
    assert not hasattr(FilesystemMiddleware, "_easy_execute_description_installed")


def test_summarization_override_uses_public_middleware_name():
    """DA-06：摘要阈值靠官方类按 ``.name`` 原位替换默认项，名字变了就会变成重复中间件。"""
    from deepagents.backends import StateBackend
    from deepagents.middleware.summarization import SummarizationMiddleware
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    middleware = SummarizationMiddleware(GenericFakeChatModel(messages=iter([])), backend=StateBackend())
    assert middleware.name == "SummarizationMiddleware"


def test_framework_artifact_paths_follow_artifacts_root():
    """DA-04：会话 default 只允许写 ``artifacts_root`` 前缀，框架产物路径必须由它派生。"""
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.middleware.filesystem import FilesystemMiddleware

    backend = CompositeBackend(default=StateBackend(), routes={}, artifacts_root="/.agent")
    middleware = FilesystemMiddleware(backend=backend)
    assert middleware._large_tool_results_prefix == "/.agent/large_tool_results"
    assert middleware._conversation_history_prefix == "/.agent/conversation_history"
