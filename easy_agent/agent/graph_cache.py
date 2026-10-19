"""D2：按「模型 + 工具集 + 提示词」复用编译图（进程内 LRU）。

设计要点
--------

1. **缓存键只包含编译期输入**：模型配置、工具指纹、系统提示词、middleware、
   HITL 配置、subagent specs、skills/permissions、memory 源、摘要阈值、
   checkpointer/store 身份。``session_id`` / workspace / thread_id 一律不进键。
2. **会话差异走运行期 ``SessionContext``**：``create_deep_agent`` 用
   ``backend`` callable + ``context_schema=SessionContext`` 建图；每次
   ``astream(..., context=agent.session_context)`` 时，backend 与工具才解析
   当前会话的工作区、用户技能、记忆文件。
3. **工具会话绑定在调用时注入**：``create_scheduled_task`` /
   ``save_expert`` / ``read_uploaded_file`` / ``access_knowledge_original``
   / ``web_search`` 的实例带有 username/session_id/workspace_dir/user_id。
   建图时用 :func:`contextualize_tools` 把它们包成 ``StructuredTool``，
   真正调用时按 ``ToolRuntime.context`` 克隆出本次会话的工具实例；因此同一
   张图可以安全地服务多个会话。
4. **无法安全共享时显式降级**：模型请求头里含 ``{session_id}``、或
   checkpointer 仍是按 session 创建的 legacy ``PersistentMemorySaver`` 时，
   ``EasyAgent._create_agent`` 不写缓存、按会话建图，并在日志中给出原因
   （见 docs/design/2026-10-18-d2-graph-reuse.md）。

官方依据（deepagents 0.6.8）
----------------------------

* ``create_deep_agent(..., backend=BackendProtocol | Callable[[ToolRuntime],
  BackendProtocol], context_schema=..., checkpointer=..., store=...)``；
* ``CompiledStateGraph.astream(..., context=...)``；
* ``FilesystemMiddleware`` / ``MemoryMiddleware`` / ``SkillsMiddleware`` 均在
  runtime 解析 backend factory，因此 backend callable 会随 context 变化。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool, StructuredTool

from easy_agent.bootstrap.env import env_str

logger = logging.getLogger("easy-agent.graph_cache")

# 改动编译图结构/键语义时递增，旧进程内缓存自然失效。
GRAPH_CACHE_VERSION = 1

_DEFAULT_MAX_ENTRIES = 32
_MAX_ENTRIES_ENV = "EASY_AGENT_GRAPH_CACHE_SIZE"
_ENABLED_ENV = "EASY_AGENT_GRAPH_CACHE"

# 工具实例上「属于运行期会话」的字段：建图时不能写死，调用时按 context 覆盖。
CONTEXT_TOOL_FIELDS = (
    "username",
    "session_id",
    "workspace_dir",
    "workspace_name",
    "user_id",
)


@dataclass(frozen=True)
class SessionContext:
    """一次运行（一个会话的一轮消息）的上下文，编译图跨会话共享。

    ``create_deep_agent(context_schema=SessionContext)`` 后，LangGraph 会把它
    原样放进 ``ToolRuntime.context`` / ``Runtime.context``；backend callable 与
    context-bound 工具都从这里取会话路径。
    """

    session_id: str
    username: str
    safe_username: str
    workspace_dir: str
    workspace_virtual_path: str
    user_skills_dir: str
    memory_file: str
    long_term_memory_file: str
    organization_id: str = ""
    shell_env: Mapping[str, str] = field(default_factory=dict)
    workspace_name: str = ""

    @property
    def sid(self) -> str:
        return (self.session_id or "new")[-5:]


def graph_cache_enabled() -> bool:
    """总开关（排障/回滚）：``EASY_AGENT_GRAPH_CACHE=0`` 关闭 D2 复用。"""
    # 不用 env_bool：它不认 ``disabled``，且把未知值当默认值，语义不同。
    raw = env_str(_ENABLED_ENV).lower()
    return raw not in ("0", "false", "no", "off", "disabled")


def _max_entries() -> int:
    raw = env_str(_MAX_ENTRIES_ENV)  # 不用 env_int：要保留非法值告警与下限 1
    if not raw:
        return _DEFAULT_MAX_ENTRIES
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "%s=%r 不是整数，使用默认 %d", _MAX_ENTRIES_ENV, raw, _DEFAULT_MAX_ENTRIES
        )
        return _DEFAULT_MAX_ENTRIES
    return max(1, value)


def _stable_json(value: Any) -> str:
    """稳定序列化：排序键 + default=str，保证同输入得到同哈希。"""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        return repr(value)


def _schema_fingerprint(schema: Any) -> Any:
    if schema is None:
        return None
    if isinstance(schema, dict):
        return schema
    if hasattr(schema, "model_json_schema"):
        try:
            return schema.model_json_schema()
        except Exception:  # noqa: BLE001 - 指纹失败时退回 repr
            pass
    return repr(schema)


def _tool_fields(tool: Any) -> Mapping[str, Any]:
    # Pydantic 2.11 起实例上的 model_fields 已弃用，优先读类属性。
    fields = getattr(type(tool), "model_fields", None)
    if isinstance(fields, Mapping):
        return fields
    fields = getattr(tool, "model_fields", None)
    if isinstance(fields, Mapping):
        return fields
    return {}


def tool_fingerprint(tool: Any) -> dict[str, Any]:
    """单工具指纹：只取「会影响编译图行为」的稳定字段。

    刻意排除 username/session_id/workspace_dir/user_id/edit_targets 等
    会话级字段；这些由运行期 context 注入，不能把同一张图拆成每会话一份。
    """
    payload: dict[str, Any] = {
        "name": getattr(tool, "name", ""),
        "description": getattr(tool, "description", "") or "",
        "class": type(tool).__name__,
        "schema": _schema_fingerprint(getattr(tool, "args_schema", None)),
    }
    fields = _tool_fields(tool)
    # 这些字段影响工具行为但不是会话级：配置变化必须触发重新建图。
    for name in (
        "config_model",
        "tag_options",
        "provider",
        "api_url",
        "search_depth",
        "topic",
        "default_time_range",
        "max_results",
    ):
        if name in fields or hasattr(tool, name):
            value = getattr(tool, name, None)
            if not callable(value):
                payload[name] = value
    # 知识库原文工具：只取非敏感的配置身份，避免把 api_key 写进键材料。
    knowledge_config = getattr(tool, "knowledge_config", None)
    if knowledge_config is not None:
        payload["knowledge_config"] = {
            "enabled": bool(getattr(knowledge_config, "enabled", False)),
            "base_url": str(getattr(knowledge_config, "base_url", "") or ""),
            "shared_dataset_id": str(
                getattr(knowledge_config, "shared_dataset_id", "") or ""
            ),
            "embedding_model": str(
                getattr(getattr(knowledge_config, "embedding", None), "model", "") or ""
            ),
        }
    if hasattr(tool, "agent_id"):
        payload["agent_id"] = getattr(tool, "agent_id", "")
    return payload


def tools_fingerprint(tools: Iterable[Any] | None) -> list[dict[str, Any]]:
    return [tool_fingerprint(tool) for tool in (tools or [])]


def middleware_fingerprint(middleware: Iterable[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in middleware or []:
        entry: dict[str, Any] = {"class": type(item).__name__}
        for name in ("run_limit", "exit_behavior"):
            if hasattr(item, name):
                entry[name] = getattr(item, name)
        result.append(entry)
    return result


def subagents_fingerprint(
    subagents: Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for spec in subagents or []:
        result.append(
            {
                "name": spec.get("name", ""),
                "description": spec.get("description", ""),
                "system_prompt": spec.get("system_prompt", ""),
                "skills": list(spec.get("skills") or []),
                "middleware": [
                    type(m).__name__ for m in (spec.get("middleware") or [])
                ],
            }
        )
    return result


def permission_fingerprint(permissions: Iterable[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for perm in permissions or []:
        result.append(
            {
                "operations": list(getattr(perm, "operations", []) or []),
                "paths": list(getattr(perm, "paths", []) or []),
                "mode": getattr(perm, "mode", ""),
            }
        )
    return result


def model_fingerprint(llm_config: Any) -> dict[str, Any]:
    """模型配置指纹；不包含 api_key（避免把密钥材料写进日志/缓存键）。"""
    retry = getattr(llm_config, "retry", None)
    return {
        "provider": getattr(llm_config, "provider", ""),
        "protocol": getattr(llm_config, "protocol", ""),
        "model": getattr(llm_config, "model", ""),
        "api_base": getattr(llm_config, "api_base", ""),
        "context_length": getattr(llm_config, "context_length", 0),
        "headers": dict(getattr(llm_config, "headers", {}) or {}),
        "retry": {
            "enabled": bool(getattr(retry, "enabled", False)),
            "max_retries": int(getattr(retry, "max_retries", 0) or 0),
        },
    }


def config_fingerprint(config: Any) -> dict[str, Any]:
    """配置级（非会话级）指纹：workspace/memories 基目录、沙箱、外部挂载等。

    这些字段不随 session 变化，但会改变 backend 行为；放进键里可避免
    测试/热更新更换配置后错误复用上一份配置编译的图。
    """
    agent_cfg = getattr(config, "agent", None)
    tools_cfg = getattr(config, "tools", None)
    file_ctx = getattr(config, "file_context", None)
    return {
        "workspace_dir": str(getattr(agent_cfg, "workspace_dir", "") or ""),
        "memories_dir": str(getattr(agent_cfg, "memories_dir", "") or ""),
        "sandbox_enabled": bool(getattr(agent_cfg, "sandbox_enabled", True)),
        "external_dirs": dict(getattr(agent_cfg, "external_dirs", {}) or {}),
        "denied_dirs": list(getattr(agent_cfg, "denied_dirs", []) or []),
        "skill_write": str(getattr(agent_cfg, "skill_write", "") or ""),
        "max_steps": int(getattr(agent_cfg, "max_steps", 0) or 0),
        "result_log_truncate": int(
            getattr(tools_cfg, "result_log_truncate", 0) or 0
        ),
        "file_context_enabled": (
            bool(getattr(file_ctx, "enabled", True)) if file_ctx is not None else None
        ),
    }


def summarization_fingerprint(config: Any) -> dict[str, Any]:
    summ = getattr(config, "summarization", None)
    return {
        "enabled": bool(getattr(summ, "enabled", True)),
        "compression_threshold": float(
            getattr(summ, "compression_threshold", 0.8) or 0.8
        ),
        "compression_target": float(getattr(summ, "compression_target", 0.1) or 0.1),
        "truncate_arg_max_length": int(
            getattr(summ, "truncate_arg_max_length", 2000) or 2000
        ),
    }


@dataclass(frozen=True)
class GraphKey:
    digest: str
    label: str
    parts: dict[str, Any]


def build_graph_key(
    *,
    model: Mapping[str, Any],
    tools: Iterable[Any] | None,
    system_prompt: str,
    middleware: Iterable[Any] | None,
    hitl: Mapping[str, Any],
    subagents: Iterable[Mapping[str, Any]] | None,
    skills: Iterable[str] | None,
    permissions: Iterable[Any] | None,
    memory: Iterable[str] | None,
    summarization: Mapping[str, Any],
    config: Mapping[str, Any],
    checkpointer_id: str,
    store_id: str,
) -> GraphKey:
    """生成编译图缓存键；所有字段均为编译期输入，不含 session/workspace。"""
    parts: dict[str, Any] = {
        "version": GRAPH_CACHE_VERSION,
        "model": dict(model),
        "tools": tools_fingerprint(tools),
        "system_prompt": system_prompt,
        "middleware": middleware_fingerprint(middleware),
        "hitl": dict(hitl),
        "subagents": subagents_fingerprint(subagents),
        "skills": list(skills or []),
        "permissions": permission_fingerprint(permissions),
        "memory": list(memory or []),
        "summarization": dict(summarization),
        "config": dict(config),
        "checkpointer": checkpointer_id,
        "store": store_id,
        "context_schema": "SessionContext",
    }
    digest = hashlib.sha256(_stable_json(parts).encode("utf-8")).hexdigest()
    model_name = str(model.get("model") or "?")
    label = (
        f"model={model_name} tools={len(parts['tools'])} "
        f"prompt={len(system_prompt)}ch memory={len(parts['memory'])}"
    )
    return GraphKey(digest=digest, label=label, parts=parts)


@dataclass
class GraphEntry:
    key: str
    graph: Any
    model: Any
    username: str
    label: str
    created_at: float
    hits: int = 0


class GraphCache:
    """进程内 LRU 编译图缓存；命中/未命中/驱逐都打日志。"""

    def __init__(self, max_entries: int | None = None) -> None:
        self.max_entries = max(1, int(max_entries or _max_entries()))
        self._entries: OrderedDict[str, GraphEntry] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.builds = 0
        self.disabled = 0

    def get(self, key: str) -> GraphEntry | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            entry.hits += 1
            self.hits += 1
            age = max(0.0, time.time() - entry.created_at)
            logger.info(
                "D2 graph-cache 命中 | key=%s | %s | 复用 %.1fs 前编译的图 | hits=%d",
                key[:12],
                entry.label,
                age,
                entry.hits,
            )
            return entry

    def put(
        self,
        key: str,
        graph: Any,
        *,
        model: Any = None,
        username: str = "",
        label: str = "",
    ) -> GraphEntry:
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None:
                existing.model = model or existing.model
                existing.label = label or existing.label
                self._entries.move_to_end(key)
                return existing
            entry = GraphEntry(
                key=key,
                graph=graph,
                model=model,
                username=username,
                label=label,
                created_at=time.time(),
            )
            self._entries[key] = entry
            self.builds += 1
            logger.info(
                "D2 graph-cache 写入 | key=%s | %s | size=%d/%d",
                key[:12],
                label,
                len(self._entries),
                self.max_entries,
            )
            while len(self._entries) > self.max_entries:
                victim_key, victim = self._entries.popitem(last=False)
                self.evictions += 1
                logger.info(
                    "D2 graph-cache LRU 驱逐 | key=%s | %s | user=%s",
                    victim_key[:12],
                    victim.label,
                    victim.username or "-",
                )
            return entry

    def invalidate_user(self, username: str) -> int:
        safe = username or ""
        with self._lock:
            victims = [k for k, e in self._entries.items() if e.username == safe]
            for key in victims:
                entry = self._entries.pop(key, None)
                if entry is not None:
                    logger.info(
                        "D2 graph-cache 用户失效 | key=%s | user=%s | %s",
                        key[:12],
                        safe,
                        entry.label,
                    )
            return len(victims)

    def clear(self) -> None:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
        if count:
            logger.info("D2 graph-cache 清空 | 移除 %d 张图", count)

    def record_disabled(self) -> None:
        """记录一次「本次请求不可复用」（模型头/legacy checkpointer/开关关闭）。"""
        with self._lock:
            self.disabled += 1

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
                "evictions": self.evictions,
                "builds": self.builds,
                "disabled": self.disabled,
                "keys": list(self._entries.keys()),
            }


_cache: GraphCache | None = None
_cache_lock = threading.Lock()


def get_agent_graph_cache() -> GraphCache:
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = GraphCache()
    return _cache


def reset_agent_graph_cache() -> None:
    """测试隔离用：清空条目并复位统计（不更换实例，便于 monkeypatch）。"""
    cache = get_agent_graph_cache()
    cache.clear()
    with cache._lock:  # noqa: SLF001 - 同模块内部状态复位
        cache.hits = cache.misses = cache.evictions = cache.builds = 0
        cache.disabled = 0


def clear_agent_graph_cache() -> None:
    """配置重载/关停时清空全部编译图。"""
    get_agent_graph_cache().clear()


# ── 运行期 context → 会话工具/backend ─────────────────────────────────────


def _context_from_runtime(runtime: Any) -> Any:
    ctx = getattr(runtime, "context", None)
    if ctx is None:
        raise RuntimeError(
            "D2 编译图需要运行期 SessionContext：请使用 "
            "astream(..., context=agent.session_context) 调用共享图"
        )
    if isinstance(ctx, Mapping):
        return SessionContext(**dict(ctx))
    return ctx


def _has_context_fields(tool: Any) -> bool:
    fields = _tool_fields(tool)
    for name in CONTEXT_TOOL_FIELDS:
        if name in fields or hasattr(tool, name):
            return True
    return False


def resolve_tool_for_context(tool: Any, context: Any) -> Any:
    """克隆工具并写入本次会话的 username/session_id/workspace 等字段。"""
    updates: dict[str, Any] = {}
    fields = _tool_fields(tool)
    if "username" in fields or hasattr(tool, "username"):
        updates["username"] = getattr(context, "username", "") or ""
    if "session_id" in fields or hasattr(tool, "session_id"):
        updates["session_id"] = getattr(context, "session_id", "") or ""
    if "workspace_dir" in fields or hasattr(tool, "workspace_dir"):
        updates["workspace_dir"] = str(getattr(context, "workspace_dir", "") or "")
    if "workspace_name" in fields or hasattr(tool, "workspace_name"):
        updates["workspace_name"] = getattr(context, "workspace_name", "") or ""
    if "user_id" in fields or hasattr(tool, "user_id"):
        updates["user_id"] = getattr(context, "safe_username", "") or ""
    if not updates:
        return tool
    model_copy = getattr(tool, "model_copy", None)
    if callable(model_copy):
        try:
            return model_copy(update=updates)
        except Exception:  # noqa: BLE001 - 非 pydantic 工具退回浅拷贝
            pass
    clone = copy.copy(tool)
    for name, value in updates.items():
        try:
            setattr(clone, name, value)
        except Exception:  # noqa: BLE001 - 只读字段忽略
            pass
    return clone


def _wrap_context_tool(tool: Any) -> BaseTool:
    """把会话绑定工具包成「调用时按 ToolRuntime.context 解析」的 StructuredTool。"""

    async def _arun(runtime: ToolRuntime, **kwargs: Any) -> Any:
        context = _context_from_runtime(runtime)
        target = resolve_tool_for_context(tool, context)
        return await target.ainvoke(kwargs)

    def _run(runtime: ToolRuntime, **kwargs: Any) -> Any:
        context = _context_from_runtime(runtime)
        target = resolve_tool_for_context(tool, context)
        return target.invoke(kwargs)

    # ``from __future__ import annotations`` 会把上面的注解变成字符串，
    # LangChain 的 ToolRuntime 注入只认真实类型（name == "runtime" 也会注入，
    # 但 _injected_args_keys 用注解判断），这里显式写回真实类。
    _arun.__annotations__["runtime"] = ToolRuntime
    _run.__annotations__["runtime"] = ToolRuntime

    wrapped = StructuredTool.from_function(
        func=_run,
        coroutine=_arun,
        name=getattr(tool, "name", "") or "tool",
        description=getattr(tool, "description", "") or "",
        args_schema=getattr(tool, "args_schema", None),
    )
    # 会话绑定工具可能带有项目自定义元数据（例如 save_expert 的 tag_options）。
    # StructuredTool 是 Pydantic 模型，普通 setattr 会拒绝未声明字段；用
    # object.__setattr__ 挂回，保持旧测试/工具消费者可读取。
    for attr in ("tag_options", "expert_tags", "tool_tags"):
        if hasattr(tool, attr):
            try:
                object.__setattr__(wrapped, attr, getattr(tool, attr))
            except Exception:  # noqa: BLE001 - 元数据复制不影响主流程
                logger.debug("D2 工具元数据复制失败 | tool=%s | attr=%s", wrapped.name, attr)
    logger.debug(
        "D2 graph-cache 工具绑定运行期 context | name=%s | class=%s",
        wrapped.name,
        type(tool).__name__,
    )
    return wrapped


def contextualize_tools(tools: Iterable[Any] | None) -> list[Any]:
    """返回可安全放进共享编译图的工具列表（会话绑定工具被包装）。"""
    result: list[Any] = []
    for tool in tools or []:
        if _has_context_fields(tool):
            result.append(_wrap_context_tool(tool))
        else:
            result.append(tool)
    return result


__all__ = [
    "CONTEXT_TOOL_FIELDS",
    "GRAPH_CACHE_VERSION",
    "GraphCache",
    "GraphEntry",
    "GraphKey",
    "SessionContext",
    "build_graph_key",
    "clear_agent_graph_cache",
    "config_fingerprint",
    "contextualize_tools",
    "get_agent_graph_cache",
    "graph_cache_enabled",
    "middleware_fingerprint",
    "model_fingerprint",
    "permission_fingerprint",
    "reset_agent_graph_cache",
    "resolve_tool_for_context",
    "subagents_fingerprint",
    "summarization_fingerprint",
    "tool_fingerprint",
    "tools_fingerprint",
]
