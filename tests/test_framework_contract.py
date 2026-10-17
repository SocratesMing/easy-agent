"""DeepAgents 框架适配契约测试。

本仓库有 3 处对 deepagents/langchain 内部实现的适配（execute 描述注入、
SummarizationMiddleware 阈值、MemorySaver 持久化）。升级框架前必须让本测试
失败并逐项复核，避免“升级后静默降级/启动期 TypeError”。

对应审计项：FW-01（版本漂移）、FW-04/FW-05（私有 API 依赖）。
"""

import importlib.metadata as md
import inspect

import pytest

# 已验证的兼容窗口：升级时先改这里，再跑全量测试与 PoC。
VERIFIED_DEEPAGENTS_MIN = (0, 6, 8)
VERIFIED_DEEPAGENTS_MAX_MAJOR_MINOR = (0, 6)
VERIFIED_LANGCHAIN_MIN = (1, 3)
VERIFIED_LANGCHAIN_MAX_MAJOR_MINOR = (1, 3)


def _version_tuple(pkg: str) -> tuple[int, ...]:
    parts = []
    for token in md.version(pkg).split(".")[:3]:
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


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
    from deepagents import create_deep_agent

    params = inspect.signature(create_deep_agent).parameters
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


def test_execute_description_injection_point_still_exists():
    from deepagents.middleware.filesystem import FilesystemMiddleware

    params = inspect.signature(FilesystemMiddleware.__init__).parameters
    assert "custom_tool_descriptions" in params, (
        "FilesystemMiddleware 不再接受 custom_tool_descriptions，"
        "hitl.install_execute_tool_description 的 monkeypatch 会失效"
    )


def test_summarization_patch_target_still_exists():
    import deepagents.graph as graph

    assert hasattr(graph, "create_summarization_middleware"), (
        "deepagents.graph.create_summarization_middleware 已改名/移除，"
        "summarization.configured_summarization 需要重新适配"
    )


def test_harness_profile_exported_for_future_migration():
    import deepagents

    assert hasattr(deepagents, "HarnessProfile"), (
        "HarnessProfile 不再导出，无法从 monkeypatch 迁移到官方机制"
    )
