"""Config-driven SummarizationMiddleware.

deepagents 0.6.8 在 ``create_deep_agent`` 内部调用
``create_summarization_middleware(model, backend)``，没有公开参数覆盖阈值。
这里只在这一次建图期间把该工厂指到官方 ``SummarizationMiddleware`` 构造器，
建完立刻还原，不再在进程启动时长期改写模块全局。
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


def summarization_settings(config, model) -> dict:
    """把 config 比例折成官方 ``SummarizationMiddleware`` 的 token 参数。"""
    from easy_agent.agent.model import resolve_context_length

    thr = float(getattr(config.summarization, "compression_threshold", 0.0) or 0.8)
    tgt = float(getattr(config.summarization, "compression_target", 0.0) or 0.1)
    truncate_max_length = int(
        getattr(config.summarization, "truncate_arg_max_length", 0) or 2000
    )
    model_name = getattr(model, "model_name", None) or getattr(model, "model", None)
    model_max = resolve_context_length(config, model_name if isinstance(model_name, str) else None)
    trigger = max(1, int(model_max * thr))
    keep = max(1, int(model_max * tgt))
    trim = max(4000, int(model_max * 0.02))
    return {
        "trigger": ("tokens", trigger),
        "keep": ("tokens", keep),
        "trim_tokens_to_summarize": trim,
        "truncate_args_settings": {
            "trigger": ("tokens", trigger),
            "keep": ("tokens", keep),
            "max_length": truncate_max_length,
            "truncation_text": "...(argument truncated)",
        },
    }


@contextmanager
def configured_summarization(config):
    """让本次 ``create_deep_agent`` 使用 config 阈值的官方摘要中间件。

    注意：``deepagents.graph.create_summarization_middleware`` 不是公开导出 API。
    升级若移除该工厂，这里显式告警并降级为框架默认阈值，而不是让建图失败。
    """
    import deepagents.graph as graph
    from deepagents.middleware.summarization import SummarizationMiddleware

    if not hasattr(graph, "create_summarization_middleware"):
        logger.error(
            "deepagents.graph.create_summarization_middleware 不存在：摘要阈值"
            "配置降级为框架默认值，请升级 easy_agent/summarization.py 适配"
        )
        yield
        return

    original = graph.create_summarization_middleware
    logged: set[str] = set()

    def factory(model, backend, **_kwargs):
        settings = summarization_settings(config, model)
        label = getattr(model, "model_name", None) or getattr(model, "model", None) or "model"
        if label not in logged:
            logged.add(str(label))
            logger.info(
                "摘要阈值 | %s | trigger=%s keep=%s",
                label,
                settings["trigger"],
                settings["keep"],
            )
        return SummarizationMiddleware(model, backend=backend, **settings)

    graph.create_summarization_middleware = factory
    try:
        yield
    finally:
        graph.create_summarization_middleware = original
