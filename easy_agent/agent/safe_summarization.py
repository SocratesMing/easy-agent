"""DeepAgents 适配：共用任务保留、失败原子性及旧坏事件恢复。

不修改共享实例或框架全局；触发、工具配对、溢出恢复和事件提交仍由框架负责。
"""

from dataclasses import replace

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware.summarization import SummarizationMiddleware as SummaryHelper
from langchain.agents.middleware.types import ExtendedModelResponse
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from easy_agent.agent.compaction_policy import TOO_LONG_SUMMARY, require_summary, summary_input, usable_event


class _TaskPreservingSummaryHelper(SummaryHelper):
    def _overflow_retry_prompt(self, messages):
        # 供应商拒绝预算后不能再次按首尾剪掉用户要求；保留旧事件并暴露失败。
        return None


class SafeSummarizationMiddleware(SummarizationMiddleware):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        helper = self._lc_helper
        # 仅在创建时构造本实例的 helper，不修改已共享的实例/框架模块。
        self._lc_helper = _TaskPreservingSummaryHelper(
            model=helper.model, trigger=helper.trigger, keep=helper.keep,
            token_counter=helper.token_counter, summary_prompt=helper.summary_prompt,
            trim_tokens_to_summarize=helper.trim_tokens_to_summarize,
            summarizer=kwargs.get("summarizer"),
        )

    @property
    def name(self) -> str:
        return "SummarizationMiddleware"

    def _create_summary(self, messages_to_summarize):
        view = summary_input(messages_to_summarize, self._lc_helper.trim_tokens_to_summarize)
        summary = super()._create_summary(view)
        if summary.strip() == TOO_LONG_SUMMARY:
            summary = super()._create_summary(view)
        return require_summary(summary)

    async def _acreate_summary(self, messages_to_summarize):
        view = summary_input(messages_to_summarize, self._lc_helper.trim_tokens_to_summarize)
        summary = await super()._acreate_summary(view)
        if summary.strip() == TOO_LONG_SUMMARY:
            summary = await super()._acreate_summary(view)
        return require_summary(summary)

    def _build_new_messages_with_path(self, summary, file_path):
        require_summary(summary)
        if not file_path:
            raise RuntimeError("历史归档失败，原上下文未修改")
        return super()._build_new_messages_with_path(summary, file_path)

    def _offload_inline_media(self, backend, messages):
        media, failed = super()._offload_inline_media(backend, messages)
        if failed:
            raise RuntimeError("媒体归档失败，原上下文未修改")
        return media, failed

    async def _aoffload_inline_media(self, backend, messages):
        media, failed = await super()._aoffload_inline_media(backend, messages)
        if failed:
            raise RuntimeError("媒体归档失败，原上下文未修改")
        return media, failed

    @staticmethod
    def _apply_event_to_messages(messages, event):
        return SummarizationMiddleware._apply_event_to_messages(messages, usable_event(event))

    @staticmethod
    def _compute_state_cutoff(event, effective_cutoff):
        return SummarizationMiddleware._compute_state_cutoff(usable_event(event), effective_cutoff)

    @staticmethod
    def _current_question(request):
        # 取 checkpoint 原话，不能把无界附件/知识片段永久塞回历史。
        source = request.state.get("messages") or request.messages
        target = (request.state.get("turn_context") or {}).get("message_id")
        if target:
            return next((m for m in source if isinstance(m, HumanMessage) and m.id == target), None)
        return next((m for m in reversed(source) if isinstance(m, HumanMessage)
                     and m.additional_kwargs.get("lc_source") != "summarization"), None)

    def _keep_question(self, request, question):
        if question is None or any(
            m is question or (question.id is not None and m.id == question.id)
            for m in request.messages
        ):
            return request
        messages = list(request.messages)
        # 摘要后、执行记录前；不破坏 AI/Tool 配对，不伪装成一轮新输入。
        index = int(bool(messages and messages[0].additional_kwargs.get("lc_source") == "summarization"))
        messages.insert(index, question)
        request = request.override(messages=messages)
        if self._over_budget(request):
            raise ContextOverflowError("保留当前用户问题后仍超过模型输入预算，请减少输入或工具上下文")
        return request

    @staticmethod
    def _clear_bad_event(response, previous):
        if previous is None or usable_event(previous) is not None:
            return response
        # 未触发新摘要时也清除坏事件，保留工具结果替换等其他更新。
        if isinstance(response, ExtendedModelResponse):
            command = response.command or Command(update={})
            update = {"_summarization_event": None, **(command.update or {})}
            return replace(response, command=replace(command, update=update))
        return ExtendedModelResponse(
            model_response=response, command=Command(update={"_summarization_event": None}),
        )

    def wrap_model_call(self, request, handler):
        question = self._current_question(request)
        response = super().wrap_model_call(request, lambda reduced: handler(self._keep_question(reduced, question)))
        return self._clear_bad_event(response, request.state.get("_summarization_event"))

    async def awrap_model_call(self, request, handler):
        question = self._current_question(request)

        async def keep_question(reduced):
            return await handler(self._keep_question(reduced, question))

        response = await super().awrap_model_call(request, keep_question)
        return self._clear_bad_event(response, request.state.get("_summarization_event"))
