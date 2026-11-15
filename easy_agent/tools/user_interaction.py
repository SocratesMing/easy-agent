"""User-facing planning interaction tool.

``ask_user`` is intentionally a HITL-only tool: the HumanInTheLoopMiddleware pauses
before execution and the frontend answers with a ``respond`` decision.  The tool
function itself must never run because a machine cannot invent the user's choices.
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator


class InteractionOption(BaseModel):
    """One selectable option shown to the user."""

    id: str = Field(
        min_length=1,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable option ID used in the answer payload.",
    )
    label: str = Field(
        min_length=1,
        max_length=80,
        description="Short, concrete option label.",
    )
    description: str = Field(
        default="",
        max_length=240,
        description="Optional trade-off, impact, or implementation detail.",
    )
    recommended: bool = Field(
        default=False,
        description="Mark at most one recommended option per question.",
    )


class InteractionQuestion(BaseModel):
    """One material planning decision that needs explicit user confirmation."""

    id: str = Field(
        min_length=1,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Stable question ID used in the answer payload.",
    )
    title: str = Field(
        min_length=1,
        max_length=120,
        description="The decision to confirm.",
    )
    description: str = Field(
        default="",
        max_length=300,
        description="Why this choice matters or what changes downstream.",
    )
    multiple: bool = Field(
        default=False,
        description="True for multi-select; false for a single choice.",
    )
    required: bool = Field(
        default=True,
        description="Whether the user must answer before continuing.",
    )
    options: list[InteractionOption] = Field(
        min_length=2,
        max_length=6,
        description="Two to six concrete choices. Do not add a generic 'other' option.",
    )

    @model_validator(mode="after")
    def _validate_unique_ids_and_recommendation(self):
        ids = [option.id for option in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError("option ids must be unique within a question")
        if sum(1 for option in self.options if option.recommended) > 1:
            raise ValueError("at most one option can be recommended")
        return self


class AskUserInput(BaseModel):
    """Structured request rendered as a confirmation card in chat."""

    title: str = Field(
        default="确认下一步方案",
        min_length=1,
        max_length=80,
        description="Card heading, for example '确认三个项目的处理方案'.",
    )
    description: str = Field(
        default="",
        max_length=600,
        description="Brief synthesis of the proposed plan and the impact of each decision.",
    )
    questions: list[InteractionQuestion] = Field(
        min_length=1,
        max_length=4,
        description="One to four material decisions. Keep the set small and non-duplicative.",
    )
    submit_label: str = Field(
        default="确认并继续",
        min_length=1,
        max_length=24,
        description="Action label shown after all required questions are answered.",
    )
    allow_note: bool = Field(
        default=True,
        description="Whether to show an optional free-form note field.",
    )

    @model_validator(mode="after")
    def _validate_unique_question_ids(self):
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question ids must be unique within one request")
        return self


ASK_USER_DESCRIPTION = """Pause and ask the user to confirm material plan choices.

Use this tool during planning or brainstorming when:
- two or more decisions are still open;
- two or more reasonable alternatives would lead to meaningfully different work;
- continuing without confirmation could waste effort or cause rework.

Rules:
- Ask about decisions, not facts you can discover yourself.
- Use one `ask_user` request for the material decisions (1-4 questions). The chat UI
  presents them sequentially as stacked cards, with one decision per card, so avoid
  repeated one-question interruptions.
- Give every question 2-6 concrete options. Put the recommended option first and
  mark it with `recommended: true` when you have a clear recommendation.
- Use `multiple: true` only when the user may legitimately combine alternatives.
- After the tool returns the user's answers, treat them as confirmed constraints,
  summarize them briefly, and continue the requested plan or workflow.
- Do not use this for security/tool approval; the runtime handles approvals separately.
"""


def _ask_user_should_never_execute(**_kwargs):
    raise RuntimeError("ask_user must be answered through the HITL respond flow")


def create_ask_user_tool() -> StructuredTool:
    """Build the stateless planning interaction tool."""

    return StructuredTool.from_function(
        func=_ask_user_should_never_execute,
        name="ask_user",
        description=ASK_USER_DESCRIPTION,
        args_schema=AskUserInput,
        infer_schema=False,
    )


INTERACTION_MODE_CONTEXT: dict[str, str] = {
    "plan": """<interaction_mode name="plan">
当前处于规划模式。先澄清目标和约束，再给出可执行计划；不要在关键选择未确认时直接执行会改变工作区或产生不可逆结果的操作。
如果计划存在多个会显著影响后续工作的分支，先在一次 `ask_user` 调用中集中列出需要确认的项目（每项 2-6 个具体候选项）。界面会逐项显示堆叠卡片，每次确认后自动进入下一项；全部确认后再继续。用户跳过非关键选择时，采用你标记的推荐项并向用户说明。
</interaction_mode>""",
    "brainstorm": """<interaction_mode name="brainstorm">
当前处于头脑风暴模式。先扩展可能性并说明取舍，再帮助用户收敛；不要过早执行最终方案。
当有多个方向值得继续发散，或需要在多个方案中确定主线时，使用 `ask_user` 集中提交 1-4 个关键选择，每个问题提供 2-6 个具体候选项。界面会逐张确认；全部选择完成后，基于结果继续深化或执行。
</interaction_mode>""",
}


def interaction_mode_context(mode: str | None) -> str:
    """Return the per-turn mode instruction; unknown values add no context."""

    return INTERACTION_MODE_CONTEXT.get(str(mode or "").strip().lower(), "")


__all__ = [
    "ASK_USER_DESCRIPTION",
    "AskUserInput",
    "INTERACTION_MODE_CONTEXT",
    "InteractionOption",
    "InteractionQuestion",
    "create_ask_user_tool",
    "interaction_mode_context",
]
