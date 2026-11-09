"""DA-11：编译后的子代理只带声明的工具（官方 Subagents › Minimize tool sets）。

改造前 office-writer / knowledge-researcher 不声明 ``tools``，会继承主代理全部工具
（MCP、web_search、save_expert、create_scheduled_task…）；知识库未启用时也照样声明
依赖 ``access_knowledge_original`` 的子代理。夹具见 ``tests/agent/conftest.py``。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool


def _tools(*names):
    return [
        StructuredTool.from_function(func=lambda: "ok", name=name, description=f"{name} tool")
        for name in names
    ]


def _compiled_tools(calls):
    return {call["name"]: sorted(t.name for t in call["tools"]) for call in calls}


def test_compiled_subagents_only_get_their_tools(build_easy_agent, compiled_agents):
    build_easy_agent(
        tools=_tools("web_search", "create_scheduled_task", "read_uploaded_file", "access_knowledge_original")
    )
    tools = _compiled_tools(compiled_agents)

    assert tools["office-writer"] == ["read_uploaded_file"]
    assert tools["knowledge-researcher"] == ["access_knowledge_original"]
    assert {"web_search", "create_scheduled_task"} <= set(tools["general-purpose"])


def test_no_knowledge_subagent_without_the_knowledge_tool(build_easy_agent, compiled_agents):
    build_easy_agent(tools=_tools("web_search"))
    tools = _compiled_tools(compiled_agents)

    assert "knowledge-researcher" not in tools
    assert tools["office-writer"] == []
