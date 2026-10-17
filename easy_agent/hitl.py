"""HITL helpers: destructive shell detection and execute tool description."""

from __future__ import annotations

import re

from deepagents.middleware.filesystem import EXECUTE_TOOL_DESCRIPTION
from langchain.agents.middleware import ToolCallRequest

from .tools.registry import default_interrupt_tool_names, name_heuristic_exempt_tools

_DESTRUCTIVE_CMD_PATTERNS = [
    r"\brm\b",
    r"\brmdir\b",
    r"\bunlink\b",
    r"\bshred\b",
    r"\bfind\s+.*\s-delete\b",
    r"\bdel\b",
    r"\berase\b",
    r"\brd\b",
    r"\bRemove-Item\b",
    r"sudo\s+rm\b",
    r"/\s*bin\s*/\s*rm\b",
]

_DIR_DELETION_PATTERNS = [
    r"\brmdir\b",
    r"\brm\b\s+(-\w*[rRd])",
    r"\bfind\s+.*\s-delete\b",
    r"\bshred\b\s+(-\w*[rR])",
    r"\brd\b(?:\s+-\w*s)?",
    r"\bdel\b(?:\s+[^&|;]*)\s/-s\b",
    r"\bRemove-Item\b[^;|&]*-Recurse\b",
]

# 默认人工审批名单：由工具注册表派生（无条件 HITL 工具），
# 内容与顺序保持改造前逐字一致（save_expert -> create_scheduled_task）。
DEFAULT_INTERRUPT_TOOLS = default_interrupt_tool_names()

# 名字启发式的豁免集合同样由注册表派生：已单独配置 HITL 的工具与只读内置工具
# 不参与「名字含 write/delete/... 即视为写操作」的判定。
_NAME_HEURISTIC_EXEMPT = name_heuristic_exempt_tools()

_MUTATING_TOOL_RE = re.compile(
    r"(write|delete|update|insert|drop|create|put|remove|save|upload|execute_sql)",
    re.I,
)

_EXECUTE_DESC_INSTALLED = False

HITL_SYSTEM_NOTE = (
    "敏感工具会触发人工审批后再执行：沙箱内的文件删除类 execute、"
    "save_expert、create_scheduled_task、access_knowledge_original 的 materialize，"
    "以及名称含 write/delete/update/insert 等写操作的 MCP 工具。"
    "execute 走 bwrap/Landlock，二者都不可用时 fail-closed，不会落到宿主机。"
)

EXECUTE_HITL_TOOL_DESCRIPTION: str = (
    "⚠️⚠️⚠️ 人工审批工具 (HUMAN-IN-THE-LOOP / HITL) ⚠️⚠️⚠️\n\n"
    "本工具在沙箱内执行 Shell 命令（Linux 优先 bwrap，其次 Landlock；"
    "二者都不可用时 fail-closed，命令会被拒绝，不会落到宿主机）。\n"
    "当命令属于「文件删除」类（如 rm / unlink / shred 删除文件）时，工具调用会触发【人工审批中断】：\n"
    "  • 调用会立即暂停，等待用户在对话界面上「批准 / 拒绝」；\n"
    "  • 在你收到用户的审批结果之前，命令【尚未真正执行】，不要假设它已成功；\n"
    "  • 若用户「拒绝」，请根据拒绝原因调整方案，不要原样重复发起被拒绝的删除命令；\n"
    "  • 目录删除（rm -r / rmdir 等）会被直接拒绝，不会进入审批流程。\n"
    "普通命令（查看文件、列出目录、编译、运行测试、安装依赖等）通常无需审批即可直接执行。\n\n"
    "—— 以下为工具的常规使用说明 ——\n\n"
    + EXECUTE_TOOL_DESCRIPTION
)


def _is_directory_deletion(command: str) -> bool:
    if not command:
        return False
    return any(re.search(pat, command) for pat in _DIR_DELETION_PATTERNS)


def looks_like_mutating_tool(name: str) -> bool:
    """Heuristic for MCP / extra tools that should pause for HITL."""
    if not name or name in _NAME_HEURISTIC_EXEMPT:
        return False
    return bool(_MUTATING_TOOL_RE.search(name))


def _is_knowledge_materialize(request: ToolCallRequest) -> bool:
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
    return str(args.get("action", "")).lower() == "materialize"


def install_execute_tool_description() -> bool:
    """Inject execute HITL text via public FilesystemMiddleware.__init__ kwarg."""
    global _EXECUTE_DESC_INSTALLED
    if _EXECUTE_DESC_INSTALLED:
        return True
    try:
        from deepagents.middleware.filesystem import FilesystemMiddleware

        original_init = FilesystemMiddleware.__init__

        def wrapped_init(self, *args, custom_tool_descriptions=None, **kwargs):
            merged = dict(custom_tool_descriptions or {})
            merged.setdefault("execute", EXECUTE_HITL_TOOL_DESCRIPTION)
            original_init(self, *args, custom_tool_descriptions=merged, **kwargs)

        FilesystemMiddleware.__init__ = wrapped_init
        FilesystemMiddleware._easy_execute_description_installed = True
        _EXECUTE_DESC_INSTALLED = True
        return True
    except Exception:  # noqa: BLE001
        return False


def execute_description_installed() -> bool:
    return _EXECUTE_DESC_INSTALLED


def _is_destructive_command(request: ToolCallRequest) -> bool:
    tool_call = request.tool_call if hasattr(request, "tool_call") else {}
    command = (tool_call.get("args", {}) if isinstance(tool_call, dict) else {}).get(
        "command", ""
    )
    if not command:
        return False
    if not any(re.search(pat, command) for pat in _DESTRUCTIVE_CMD_PATTERNS):
        return False
    if _is_directory_deletion(command):
        return False
    return True
