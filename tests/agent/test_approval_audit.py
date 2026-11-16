"""审批模式日志：auto 打「跳过人工审批」，manual 不介入（2026-11-13）。

auto 模式下 ``interrupt_on`` 被摘空、框架不装 ``HumanInTheLoopMiddleware``，
工具执行链路上没有任何审批痕迹；本组用例锁住审计中间件补的那条日志，
以及「只对本该审批的调用记 info」这条降噪规则。
"""

import logging
from types import SimpleNamespace

from easy_agent.agent.approval_audit import ApprovalAuditMiddleware
from easy_agent.agent.hitl import (
    AUTO_APPROVAL_SKIPPED_NOTE,
    MANUAL_APPROVAL_DONE_NOTE,
    brief_args,
)


def _request(name: str, args: dict | None = None) -> SimpleNamespace:
    """最小 ToolCallRequest 替身：中间件只读 tool_call。"""
    return SimpleNamespace(tool_call={"name": name, "args": args or {}})


def _run(middleware: ApprovalAuditMiddleware, request) -> str:
    """同步跑一次工具调用，返回 handler 的产物（验证中间件不阻断调用）。"""
    return middleware.wrap_tool_call(request, lambda _req: "ok")


def _skipped_logs(caplog, level: int = logging.INFO) -> list[str]:
    """含「跳过人工审批」文案、且不低于 ``level`` 的日志（调试级另计）。"""
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= level and AUTO_APPROVAL_SKIPPED_NOTE in r.getMessage()
    ]


def test_auto_mode_logs_skipped_approval_for_delete(caplog):
    """auto 下删除文件：日志明确写出「用户已选择自动审批，跳过人工审批」+ 工具 + 路径。"""
    middleware = ApprovalAuditMiddleware(approval_mode="auto")
    with caplog.at_level(logging.INFO, logger="easy-agent.hitl"):
        assert _run(middleware, _request("delete", {"file_path": "/workspace/a.txt"})) == "ok"

    logs = _skipped_logs(caplog)
    assert len(logs) == 1
    assert "delete" in logs[0]
    assert "/workspace/a.txt" in logs[0]


def test_manual_mode_logs_nothing(caplog):
    """人工审批模式由恢复阶段的「用户已人工审批」负责，这里一条都不打。"""
    middleware = ApprovalAuditMiddleware(approval_mode="manual")
    with caplog.at_level(logging.DEBUG, logger="easy-agent.hitl"):
        _run(middleware, _request("delete", {"file_path": "/workspace/a.txt"}))

    assert _skipped_logs(caplog) == []


def test_auto_mode_ignores_tools_outside_approval_list(caplog):
    """只读内置工具本来就不进审批，不该刷「跳过审批」的日志。"""
    middleware = ApprovalAuditMiddleware(approval_mode="auto")
    with caplog.at_level(logging.DEBUG, logger="easy-agent.hitl"):
        _run(middleware, _request("ls", {"path": "/workspace"}))

    assert _skipped_logs(caplog) == []


def test_auto_mode_logs_only_deletion_commands_for_execute(caplog):
    """execute 只在「删除文件」时才真正触发审批，普通命令不该记 info。"""
    middleware = ApprovalAuditMiddleware(approval_mode="auto")
    with caplog.at_level(logging.INFO, logger="easy-agent.hitl"):
        _run(middleware, _request("execute", {"command": "ls -la /workspace"}))
        _run(middleware, _request("execute", {"command": "rm /workspace/a.txt"}))

    logs = _skipped_logs(caplog)
    assert len(logs) == 1
    assert "rm /workspace/a.txt" in logs[0]


def test_auto_mode_logs_knowledge_materialize_only(caplog):
    """知识库工具只有 materialize 会触发审批，检索不记。"""
    middleware = ApprovalAuditMiddleware(approval_mode="auto")
    with caplog.at_level(logging.INFO, logger="easy-agent.hitl"):
        _run(middleware, _request("access_knowledge_original", {"action": "search"}))
        _run(middleware, _request("access_knowledge_original", {"action": "materialize"}))

    logs = _skipped_logs(caplog)
    assert len(logs) == 1
    assert "materialize" in logs[0]


def test_auto_mode_logs_mutating_mcp_but_not_queries(caplog):
    """MCP 在 auto 下全部放行：写操作记 info，查询类只留 debug（避免刷屏）。"""
    middleware = ApprovalAuditMiddleware(approval_mode="auto")
    with caplog.at_level(logging.DEBUG, logger="easy-agent.hitl"):
        _run(middleware, _request("mcp__db__list_tables"))
        _run(middleware, _request("mcp__db__delete_rows", {"table": "t"}))

    logs = _skipped_logs(caplog)
    assert len(logs) == 1
    assert "mcp__db__delete_rows" in logs[0]


def test_approval_tools_override_limits_audit(caplog):
    """审批名单可被配置覆写：名单里没有 delete 时不记（与 interrupt_on 同源）。"""
    middleware = ApprovalAuditMiddleware(
        approval_mode="auto", approval_tools=["save_expert"]
    )
    with caplog.at_level(logging.INFO, logger="easy-agent.hitl"):
        _run(middleware, _request("delete", {"file_path": "/workspace/a.txt"}))

    assert _skipped_logs(caplog) == []


def test_log_wording_is_distinguishable_between_modes():
    """两条文案都含「审批」但互不为前缀，日志检索能区分是谁放行的。"""
    assert AUTO_APPROVAL_SKIPPED_NOTE not in MANUAL_APPROVAL_DONE_NOTE
    assert MANUAL_APPROVAL_DONE_NOTE not in AUTO_APPROVAL_SKIPPED_NOTE


def test_brief_args_truncates_and_degrades():
    assert brief_args(None) == "-"
    assert brief_args({}) == "-"
    assert brief_args({"file_path": "/workspace/a.txt"}) == "{'file_path': '/workspace/a.txt'}"
    long_text = brief_args({"command": "x" * 400})
    assert long_text.endswith("…")
    assert len(long_text) == 301
