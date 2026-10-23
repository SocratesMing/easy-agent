"""HITL 审批策略测试：破坏性命令与目录删除识别、写操作类工具判定、
模型 profile 查找键（FW-04）和 execute 的中断条件（FW-12）。
"""

from types import SimpleNamespace

from easy_agent.agent.hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    _is_destructive_command,
    _is_directory_deletion,
    _is_knowledge_materialize,
    looks_like_mutating_tool,
)


def test_execute_description_mentions_sandbox():
    assert "bwrap" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "fail-closed" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "宿主机直接执行" not in EXECUTE_HITL_TOOL_DESCRIPTION


def test_file_rm_is_destructive_dir_rm_is_not():
    req = SimpleNamespace(tool_call={"args": {"command": "rm /workspace/a.txt"}})
    assert _is_destructive_command(req) is True
    assert _is_directory_deletion("rm -r /workspace/dir") is True
    req_dir = SimpleNamespace(tool_call={"args": {"command": "rm -r /workspace/dir"}})
    assert _is_destructive_command(req_dir) is False


def test_mutating_mcp_and_knowledge_materialize():
    assert looks_like_mutating_tool("mysql_execute_sql") is True
    assert looks_like_mutating_tool("read_file") is False
    req = SimpleNamespace(tool_call={"args": {"action": "materialize", "document_id": "d1"}})
    assert _is_knowledge_materialize(req) is True
    req_list = SimpleNamespace(tool_call={"args": {"action": "list"}})
    assert _is_knowledge_materialize(req_list) is False


# ── FW-04：HarnessProfile 查找键 ─────────────────────────────────────────
# 键必须用模型实例的 LangChain provider（openai/anthropic），不是配置里的网关名
# （deepseek/minimax），否则 create_deep_agent 对预构建模型实例查不到 profile。


def test_profile_keys_follow_langchain_provider_not_gateway_name():
    from easy_agent.agent.hitl import _configured_model_profile_keys

    config = SimpleNamespace(
        models={
            "deepseek": SimpleNamespace(
                provider="deepseek", protocol="openai", model="deepseek-v4-flash"
            ),
            "claude": SimpleNamespace(
                provider="minimax", protocol="anthropic", model="claude-sonnet-4-6"
            ),
        },
        # active 模型与 models 重复时只保留一个键
        llm=SimpleNamespace(
            provider="deepseek", protocol="openai", model="deepseek-v4-flash"
        ),
    )
    assert _configured_model_profile_keys(config) == (
        "openai:deepseek-v4-flash",
        "anthropic:claude-sonnet-4-6",
    )


def test_profile_key_handles_colon_identifier_and_missing_fields():
    from easy_agent.agent.hitl import _model_profile_key

    assert (
        _model_profile_key(SimpleNamespace(protocol="openai", model="vendor:model-x"))
        == "vendor:model-x"
    )
    assert _model_profile_key(SimpleNamespace(protocol="openai", model="")) is None
    # protocol 缺省/为空时与 model.create_model 一致按 openai 处理
    assert (
        _model_profile_key(SimpleNamespace(protocol="", model="m")) == "openai:m"
    )
    assert _model_profile_key(None) is None


def test_profile_keys_without_config_fall_back_to_provider_keys():
    from easy_agent.agent.hitl import _configured_model_profile_keys

    # 仅兼容旧的无参调用；正常路径总是传入 Config。
    assert _configured_model_profile_keys(None) == ("openai", "anthropic")


# ── FW-12：动态名单下的目录删除策略（直接拒绝，不进入审批） ──────────────
def test_build_interrupt_config_keeps_execute_conditional_for_directory_deletion():
    from easy_agent.agent.hitl import build_interrupt_config

    cfg = build_interrupt_config([])
    when = cfg["execute"]["when"]

    file_req = SimpleNamespace(tool_call={"args": {"command": "rm /workspace/a.txt"}})
    dir_req = SimpleNamespace(tool_call={"args": {"command": "rm -r /workspace/dir"}})

    assert when(file_req) is True
    # 目录删除不进 HITL，由 _PathTranslatingShell.execute 直接拒绝（策略层）。
    assert when(dir_req) is False
    assert _is_directory_deletion(dir_req.tool_call["args"]["command"]) is True


def test_build_interrupt_config_uninjected_tools_absent():
    from easy_agent.agent.hitl import build_interrupt_config

    cfg = build_interrupt_config(["mcp_write_file"])
    assert set(cfg) == {"execute", "delete", "mcp_write_file"}
    assert "access_knowledge_original" not in cfg
    assert "save_expert" not in cfg
