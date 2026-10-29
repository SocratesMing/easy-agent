"""Config-driven SummarizationMiddleware（DA-06）。

deepagents 0.7 起，``middleware=`` 里 ``.name`` 与内置默认项相同的实例会**原位替换**
该默认项（官方 changelog "Override a default middleware instance"），声明式子代理 spec
的 ``middleware`` 同理。因此主代理与每个子代理 spec 各放一份按配置阈值构造的官方
``SummarizationMiddleware``，不再在建图期间替换框架私有的
``deepagents.graph.create_summarization_middleware``（模块全局替换还有线程安全隐患）。

``summarization.enabled=false`` 时不能"不传"——框架默认栈仍会挂一份；改为构造同名实例
并把 trigger/keep 抬到不可达，等价关闭压缩与参数截断。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_logged_models: set[str] = set()


def summarization_settings(config, model_key: str | None = None) -> dict:
    """把 config 比例折成官方 ``SummarizationMiddleware`` 的 token 参数。

    窗口取 ``model_key`` 对应模型的 ``context_length``。``model_key`` 是 ``config.models``
    的配置键（会话在输入框选的模型，即 ``EasyAgent.model_name``），与推给前端的
    ``context_length`` / ``auto_compress_tokens`` 同一口径；不传时用当前默认模型。
    不能拿 ``model.model_name`` 去查：那是 API 模型名（如 ``deepseek-v4.1-flash``），
    与配置键（``deepseek``）对不上，会一律退回默认模型。
    """
    from easy_agent.agent.model import resolve_context_length

    thr = float(getattr(config.summarization, "compression_threshold", 0.0) or 0.8)
    tgt = float(getattr(config.summarization, "compression_target", 0.0) or 0.1)
    truncate_max_length = int(
        getattr(config.summarization, "truncate_arg_max_length", 0) or 2000
    )
    if not bool(getattr(config.summarization, "enabled", True)):
        # 官方 create_deep_agent 的默认栈总会挂摘要中间件，无法靠"不传"关闭；
        # 这里把阈值抬到不可达，等价关闭压缩与参数截断（同名替换默认项仍在）。
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
    """按 config 阈值构造官方摘要中间件；每个代理（主代理 / 各子代理）各用一份。

    ``model_key``：会话所用模型的配置键，阈值按它的窗口计算（见 :func:`summarization_settings`）。
    ``render_turn=True``（只给主代理）：先把本轮用户消息换成 ``turn_context`` 里的渲染
    内容再计 token / 摘要，见 ``agent/turn_context.py``（DA-03）。
    """
    from deepagents.middleware.summarization import SummarizationMiddleware

    from easy_agent.agent.turn_context import TurnAwareSummarizationMiddleware

    settings = summarization_settings(config, model_key)
    label = str(getattr(model, "model_name", None) or getattr(model, "model", None) or "model")
    if model_key:
        label = f"{model_key}/{label}"
    if label not in _logged_models:
        _logged_models.add(label)
        logger.info(
            "摘要阈值 | %s | trigger=%s keep=%s", label, settings["trigger"], settings["keep"]
        )
    cls = TurnAwareSummarizationMiddleware if render_turn else SummarizationMiddleware
    return cls(model, backend=backend, **settings)
