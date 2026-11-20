"""按配置阈值构造摘要中间件，按同名原位替换框架默认 SummarizationMiddleware。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_logged_models: set[str] = set()


def summarization_settings(config, model_key: str | None = None, *, manual: bool = False) -> dict:
    """将配置比例换算为 SummarizationMiddleware 所需的 token 阈值参数。"""
    from easy_agent.agent.model import resolve_context_length

    thr = float(getattr(config.summarization, "compression_threshold", 0.0) or 0.8)
    tgt = float(getattr(config.summarization, "compression_target", 0.0) or 0.1)
    truncate_max_length = int(
        getattr(config.summarization, "truncate_arg_max_length", 0) or 2000
    )
    if not manual and not bool(getattr(config.summarization, "enabled", True)):
        # 框架默认总会挂载摘要中间件，关闭时将阈值抬至不可达以禁用压缩。
        far = 1 << 40
        return {
            "trigger": ("tokens", far),
            "keep": ("tokens", far),
            "trim_tokens_to_summarize": far,
            "truncate_args_settings": {
                "trigger": ("tokens", far),
                "keep": ("tokens", far),
                "max_length": truncate_max_length,
                "truncation_text": "...(argument truncated)",
            },
        }
    model_max = resolve_context_length(config, model_key)
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


def build_summarization_middleware(
    config, model, backend, *, model_key: str | None = None, render_turn: bool = False
):
    """按配置阈值构造安全摘要中间件；主代理开启 render_turn 先渲染本轮上下文再计 token。"""
    from easy_agent.agent.safe_summarization import SafeSummarizationMiddleware
    from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware
    from easy_agent.agent.token_counting import count_context_tokens

    settings = summarization_settings(config, model_key)
    label = str(getattr(model, "model_name", None) or getattr(model, "model", None) or "model")
    if model_key:
        label = f"{model_key}/{label}"
    if label not in _logged_models:
        _logged_models.add(label)
        logger.info(
            "摘要阈值 | %s | trigger=%s keep=%s", label, settings["trigger"], settings["keep"]
        )
    cls = TurnAwareSummarizationMiddleware if render_turn else SafeSummarizationMiddleware
    return cls(model, backend=backend, token_counter=count_context_tokens, **settings)
