"""Planning confirmation tool and per-turn mode context."""

import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from pydantic import ValidationError

from easy_agent.tools.user_interaction import (
    AskUserInput,
    create_ask_user_tool,
    interaction_mode_context,
)
from easy_agent.services.streaming.approvals import HitlInterrupt, plan_resume


def _request(**overrides):
    payload = {
        "title": "确认三个项目",
        "questions": [
            {
                "id": "project-a",
                "title": "项目 A 采用哪种方案？",
                "options": [
                    {"id": "fast", "label": "快速版本", "recommended": True},
                    {"id": "full", "label": "完整版本"},
                ],
            },
            {
                "id": "project-b",
                "title": "项目 B 覆盖哪些端？",
                "multiple": True,
                "options": [
                    {"id": "web", "label": "Web"},
                    {"id": "mobile", "label": "移动端"},
                ],
            },
        ],
    }
    payload.update(overrides)
    return AskUserInput.model_validate(payload)


def test_ask_user_schema_accepts_multi_question_choices():
    request = _request()
    assert [question.id for question in request.questions] == [
        "project-a",
        "project-b",
    ]
    assert request.questions[1].multiple is True
    assert request.questions[0].options[0].recommended is True


@pytest.mark.parametrize(
    "question",
    [
        {
            "id": "one-option",
            "title": "只有一个选项",
            "options": [{"id": "only", "label": "唯一"}],
        },
        {
            "id": "duplicate-options",
            "title": "重复 ID",
            "options": [
                {"id": "same", "label": "A"},
                {"id": "same", "label": "B"},
            ],
        },
    ],
)
def test_ask_user_schema_rejects_unusable_choice_sets(question):
    with pytest.raises(ValidationError):
        _request(questions=[question])


def test_ask_user_tool_is_named_and_respond_only_aware():
    tool = create_ask_user_tool()
    assert tool.name == "ask_user"
    assert tool.args_schema is AskUserInput
    assert "objection" not in tool.description.lower()
    assert "2-6 concrete options" in tool.description


def test_interaction_mode_context_is_explicit_and_scoped():
    assert "规划模式" in interaction_mode_context("plan")
    assert "头脑风暴模式" in interaction_mode_context("brainstorm")
    assert interaction_mode_context("normal") == ""
    assert interaction_mode_context("unknown") == ""


def test_respond_decision_resumes_ask_user_interrupt():
    interrupt = HitlInterrupt(
        interrupt_id="ask-1",
        action_requests=[
            {"name": "ask_user", "args": {"questions": []}},
        ],
        review_configs=[
            {"action_name": "ask_user", "allowed_decisions": ["respond"]},
        ],
    )
    plan = plan_resume(
        [interrupt],
        [{"type": "respond", "message": '{"answers":{"project":"fast"}}'}],
    )
    assert plan.resume == {
        "decisions": [
            {
                "type": "respond",
                "message": '{"answers":{"project":"fast"}}',
            }
        ]
    }


async def test_deepagents_ask_user_interrupt_resumes_with_respond():
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    class ToolCallingFake(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            message = self._generate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ).generations[0].message
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content=message.content,
                    id=message.id,
                    tool_call_chunks=[
                        {
                            "name": tool_call["name"],
                            "args": json.dumps(tool_call["args"]),
                            "id": tool_call["id"],
                            "index": index,
                        }
                        for index, tool_call in enumerate(message.tool_calls or [])
                    ],
                )
            )

    request = _request().model_dump()
    model = ToolCallingFake(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "ask_user",
                            "args": request,
                            "id": "ask-call-1",
                        }
                    ],
                ),
                AIMessage(content="已按确认结果继续。"),
            ]
        )
    )
    graph = create_deep_agent(
        model=model,
        backend=StateBackend(),
        tools=[create_ask_user_tool()],
        checkpointer=MemorySaver(),
        interrupt_on={"ask_user": {"allowed_decisions": ["respond"]}},
    )
    config = {"configurable": {"thread_id": "plan-confirmation"}}

    await graph.ainvoke(
        {"messages": [HumanMessage(content="先把三个项目的方案确认一下")]},
        config,
    )
    state = await graph.aget_state(config)
    interrupts = [
        item
        for task in state.tasks
        for item in (getattr(task, "interrupts", None) or [])
    ]
    assert len(interrupts) == 1
    assert interrupts[0].value["action_requests"][0]["name"] == "ask_user"

    answer = '{"answers":{"project-a":"fast","project-b":["web"]}}'
    final = await graph.ainvoke(
        Command(
            resume={
                "decisions": [
                    {"type": "respond", "message": answer},
                ]
            }
        ),
        config,
    )
    tool_messages = [
        message for message in final["messages"] if isinstance(message, ToolMessage)
    ]
    assert tool_messages[-1].name == "ask_user"
    assert tool_messages[-1].content == answer
    assert final["messages"][-1].content == "已按确认结果继续。"
