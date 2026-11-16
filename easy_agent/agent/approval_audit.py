"""自动审批模式的审计日志：给「没弹审批」留一条可查的痕迹（2026-11-13）。

``auto`` 模式下审批名单被摘空，框架**不安装** ``HumanInTheLoopMiddleware``，
整条工具链路上没有任何审批节点。于是日志里只剩「工具直接执行」——用户和运维都无法
从日志判断这是「用户设置了自动审批」还是「审批配置没生效」。

本中间件只做一件事：在本该进审批的调用**执行前**补一条 info 日志

    用户已选择自动审批，跳过人工审批 | 工具: delete | 参数: {'file_path': ...}

只对本该审批的调用记 info，其余压到 debug，避免把日志刷满：

* 注册表 HITL 工具按「本轮审批名单 + 注册表条件谓词」判定。``execute`` 只有删除类
  命令、``access_knowledge_original`` 只有 ``materialize`` 才真正会触发审批，普通
  ``ls`` / ``cat`` / 知识库检索与审批无关，记 debug 即可；
* MCP / 动态注入工具在 auto 下一律放行（见 ``hitl.AUTO_APPROVES_MCP_TOOLS``），
  只有名字像写操作时才记 info——查询类工具一轮几十次，记 info 会淹没真正的删除操作。

``manual`` 模式的「用户已人工审批」不在本模块：那条日志在恢复阶段由
``services/streaming/orchestrator`` 打，那里才知道每个操作收到的决策。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from easy_agent.agent.hitl import (
    APPROVAL_MODE_MANUAL,
    AUTO_APPROVAL_SKIPPED_NOTE,
    DEFAULT_APPROVAL_TOOLS,
    HITL_WHEN_PREDICATES,
    brief_args,
    is_auto_approval,
    looks_like_mutating_tool,
    normalize_approval_mode,
)
from easy_agent.tools.registry import get_tool_spec

logger = logging.getLogger("easy-agent.hitl")


def _call_name_args(request: Any) -> tuple[str, object]:
    """取本次工具调用的名字与参数；结构不符时返回 ``("", {})``。"""
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, dict):
        return "", {}
    return str(tool_call.get("name") or ""), tool_call.get("args") or {}


def _thread_id(request: Any) -> str:
    """尽力从运行配置取 thread_id 作日志前缀；取不到返回空串（省略前缀）。

    中间件进共享编译图、不绑定会话，所以只能从本次运行的 config 里读。
    """
    config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
    else:
        getter = getattr(config, "get", None)
        configurable = getter("configurable") if callable(getter) else None
    if not isinstance(configurable, Mapping):
        return ""
    return str(configurable.get("thread_id") or "")


class ApprovalAuditMiddleware(AgentMiddleware):
    """auto 模式下补「跳过人工审批」日志；manual 模式下完全不介入。"""

    def __init__(
        self,
        *,
        approval_mode: str = APPROVAL_MODE_MANUAL,
        approval_tools: Iterable[str] | None = None,
    ) -> None:
        """``approval_tools`` 是本轮审批名单（config.yaml 的 ``hitl.tools``），
        ``None`` 用注册表默认名单。名单外的工具本来就不进审批，不该记日志。
        """
        super().__init__()
        self.approval_mode = normalize_approval_mode(approval_mode)
        names = DEFAULT_APPROVAL_TOOLS if approval_tools is None else approval_tools
        self.approval_tools = frozenset(str(name) for name in names)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._log_skipped(request)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        self._log_skipped(request)
        return await handler(request)

    def _log_skipped(self, request: Any) -> None:
        """工具执行前记一条：这次调用本该审批，但用户选了自动审批。

        审计日志不能影响业务：任何取值/判定异常都吞成 debug，绝不打断工具调用。
        """
        if not is_auto_approval(self.approval_mode):
            return
        try:
            self._emit_log(request)
        except Exception as exc:  # noqa: BLE001 - 日志失败不该阻断工具执行
            logger.debug("审批审计日志跳过（%s: %s）", type(exc).__name__, exc)

    def _emit_log(self, request: Any) -> None:
        name, args = _call_name_args(request)
        if not name:
            return
        thread = _thread_id(request)
        prefix = f"[{thread}] " if thread else ""

        spec = get_tool_spec(name)
        if spec is None:
            # MCP / 动态注入：auto 下全部放行，只有疑似写操作值得记 info
            level = logging.INFO if looks_like_mutating_tool(name) else logging.DEBUG
            logger.log(
                level,
                "%s%s | 工具: %s | 参数: %s",
                prefix,
                AUTO_APPROVAL_SKIPPED_NOTE,
                name,
                brief_args(args),
            )
            return

        if name not in self.approval_tools:
            return  # 不在审批名单的只读内置工具，与审批无关
        predicate = (
            HITL_WHEN_PREDICATES.get(spec.hitl_predicate)
            if spec.hitl_predicate
            else None
        )
        if predicate is not None and not predicate(request):
            logger.debug(
                "%s自动审批模式：本次调用本身不需审批 | 工具: %s | 参数: %s",
                prefix,
                name,
                brief_args(args),
            )
            return
        logger.info(
            "%s%s | 工具: %s | 参数: %s",
            prefix,
            AUTO_APPROVAL_SKIPPED_NOTE,
            name,
            brief_args(args),
        )
