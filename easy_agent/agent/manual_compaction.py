"""DeepAgents 0.7.28 手动摘要适配层。私有接口仅在此使用，不调用主模型 handler。"""

from importlib.metadata import PackageNotFoundError, version

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import HumanMessage, SystemMessage, get_buffer_string, trim_messages

from easy_agent.agent.model import resolve_context_length
from easy_agent.agent.summarization import summarization_settings
from easy_agent.agent.token_counting import count_context_tokens


VERIFIED_VERSIONS = {"deepagents": "0.7.28", "langgraph": "1.2.16"}
_REQUIRED_METHODS = (
    "_apply_event_to_messages", "_determine_cutoff_index", "_partition_messages",
    "_aoffload_inline_media", "_acreate_summary", "_get_session_id",
    "_aoffload_to_backend", "_build_new_messages_with_path", "_compute_state_cutoff",
)


def compatibility_reason() -> str | None:
    """状态查询和执行共用的能力探测；私有协议只接受真实图验证过的版本。"""
    for package, expected in VERIFIED_VERSIONS.items():
        try:
            installed = version(package)
        except PackageNotFoundError:
            return f"当前环境不支持手动压缩：缺少 {package} 版本信息（需要 {expected}）"
        if installed != expected:
            return f"当前环境不支持手动压缩：{package} {installed} 尚未验证（需要 {expected}）"
    missing = [name for name in _REQUIRED_METHODS if not callable(getattr(SummarizationMiddleware, name, None))]
    if missing:
        return "当前环境不支持手动压缩：摘要适配接口不可用（" + ", ".join(missing) + "）"
    return None


def _require_compatible() -> None:
    if reason := compatibility_reason():
        raise RuntimeError(reason)


def _bounded_tool_history(messages: list, budget: int) -> list:
    """工具循环内没有近期 human 时，按文本裁剪，保留目标和最新工具结果。

    框架的 start_on='human' 会把超预算的连续工具链裁成空列表。这里只改变
    摘要输入的文本视图；完整消息配对仍保留在 checkpoint 和归档中。
    """
    text = HumanMessage(content=get_buffer_string(messages, format="xml"))
    # 工具结果可能整段没有换行；默认按行拆分仍会丢弃整个超长结果。
    common = {"token_counter": count_context_tokens, "allow_partial": True, "text_splitter": list}
    head = trim_messages([text], max_tokens=max(1, budget // 4), strategy="first", **common)
    tail = trim_messages([text], max_tokens=max(1, budget * 3 // 4 - 100), strategy="last", **common)
    return [HumanMessage(content=(head[0].text if head else "")
                         + "\n[... middle of tool history omitted ...]\n"
                         + (tail[0].text if tail else ""))]


def _context_view(agent, messages: list, values: dict, *, backend=None):
    _require_compatible()
    settings = summarization_settings(agent.config, agent.model_name, manual=True)
    middleware = SummarizationMiddleware(
        agent.model, backend=backend if backend is not None else agent._build_backend([]), token_counter=count_context_tokens,
        **settings,
    )
    # 不渲染旧 turn_context；历史思考与下一轮请求保持相同口径。
    effective = middleware._apply_event_to_messages(messages, values.get("_summarization_event"))
    return settings, middleware, effective


def _estimate(agent, effective: list) -> dict:
    system = [SystemMessage(content=agent.system_prompt)] if agent.system_prompt else []
    tokens = count_context_tokens([*system, *effective])
    return {
        "before_tokens": tokens, "after_tokens": tokens,
        "context_length": resolve_context_length(agent.config, agent.model_name),
        "estimate_basis": "有效上下文及基础系统提示估算；不含动态提示与工具 schema，不代表实际 usage",
    }


def estimate_context(agent, messages: list, values: dict) -> dict:
    """无需再次摘要时也返回当前估算，避免 UI 回退到压缩前的供应商用量。"""
    _, _, effective = _context_view(agent, messages, values)
    return _estimate(agent, effective)


async def prepare_compaction(agent, messages: list, values: dict) -> dict | None:
    _require_compatible()
    backend = agent._build_backend([])
    settings, middleware, effective = _context_view(agent, messages, values, backend=backend)
    previous = values.get("_summarization_event")
    cutoff = middleware._determine_cutoff_index(effective)
    if cutoff <= (1 if previous else 0):
        return None
    old, kept = middleware._partition_messages(effective, cutoff)
    media, failed = await middleware._aoffload_inline_media(backend, old)
    if failed:
        raise RuntimeError("媒体归档失败，原上下文未修改")
    summary = await middleware._acreate_summary(media)
    if summary == "Previous conversation was too long to summarize.":
        summary = await middleware._acreate_summary(
            _bounded_tool_history(media, settings["trim_tokens_to_summarize"])
        )
    if not summary.strip() or summary in (
        "No previous conversation history.", "Previous conversation was too long to summarize."
    ):
        raise RuntimeError("摘要模型未返回有效摘要")
    # 每次操作写独立归档；允许提交前失败留下孤立文件，失败路径不回滚文件写入。
    archive_id = middleware._get_session_id({})
    path = await middleware._aoffload_to_backend(backend, media, archive_id)
    if not path:
        raise RuntimeError("历史归档失败，原上下文未修改")
    summary_message = middleware._build_new_messages_with_path(summary, path)[0]
    system = [SystemMessage(content=agent.system_prompt)] if agent.system_prompt else []
    return {
        "event": {
            "cutoff_index": middleware._compute_state_cutoff(previous, cutoff),
            "summary_message": summary_message, "file_path": path,
        },
        "archive_id": archive_id,
        **_estimate(agent, effective),
        "after_tokens": count_context_tokens([*system, summary_message, *kept]),
    }


def terminal_update_node(graph) -> str:
    """从真实编译图选择唯一直接终止节点，拒绝无法证明终止的图。"""
    edges = graph.get_graph().edges
    nodes = {e.source for e in edges if e.target == "__end__" and not e.conditional}
    nodes = {n for n in nodes if all(e.target == "__end__" for e in edges if e.source == n)}
    if len(nodes) != 1:
        raise RuntimeError("当前图没有可验证的独立终止节点，无法手动压缩")
    return nodes.pop()
