"""MCP 数据来源标注（防幻觉）与来源透传。

覆盖：
1. ``_decorate_mcp_result``：工具返回内容首尾注入「数据来源 + 使用规则」；
2. ``_prepare_mcp_tool``：包装 coroutine、写 metadata、描述前缀，且幂等；
3. ``collect_mcp_tool_sources``：只收集 MCP 工具，忽略内置工具；
4. ``StreamProcessor``：tool_call / tool_result / done.blocks 均携带 source。
"""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from easy_agent.services import mcp
from easy_agent.services.stream_processor import StreamProcessor

SOURCE = {
    "server": "mysql",
    "display_name": "MySQL 数据库",
    "tool": "query_orders",
    "called_at": "2026-10-19T10:00:00+08:00",
}


def _fake_tool(name: str = "query_orders", description: str = "查询订单") -> SimpleNamespace:
    async def _call(**kwargs):
        return '[{"id": 1, "amount": 100}]'

    return SimpleNamespace(
        name=name,
        description=description,
        metadata=None,
        coroutine=_call,
        func=None,
    )


# ── 1. 返回内容注入来源 ────────────────────────────────────────────────


def test_decorate_string_result_adds_source_and_usage_rule():
    out = mcp._decorate_mcp_result("id,amount\n1,100", SOURCE)
    assert "【数据来源】连接器：MySQL 数据库｜工具：query_orders｜调用时间：" in out
    assert "禁止推测、补全" in out
    assert "id,amount\n1,100" in out
    assert out.rstrip().endswith("【原始数据结束】")


def test_decorate_list_result_keeps_multimodal_blocks():
    blocks = [
        {"type": "text", "text": "id=1"},
        {"type": "image", "data": "AAA"},
    ]
    out = mcp._decorate_mcp_result(blocks, SOURCE)
    assert isinstance(out, list) and len(out) == 2
    assert out[0]["text"].startswith("【数据来源】")
    assert out[0]["text"].endswith("【原始数据结束】")
    assert out[1] == {"type": "image", "data": "AAA"}


def test_decorate_non_text_result_is_stringified_with_source():
    out = mcp._decorate_mcp_result({"rows": []}, SOURCE)
    assert "【数据来源】" in out and "{'rows': []}" in out


# ── 2. 工具包装 ────────────────────────────────────────────────────────


async def test_prepare_tool_wraps_coroutine_and_sets_metadata():
    tool = _fake_tool()
    meta = mcp._prepare_mcp_tool(tool, "mysql", "MySQL 数据库")

    assert meta["mcp_server"] == "mysql"
    assert tool.metadata["mcp_display_name"] == "MySQL 数据库"
    assert tool.description.startswith("[连接器：MySQL 数据库]")

    out = await tool.coroutine()
    assert "【数据来源】连接器：MySQL 数据库" in out
    assert '[{"id": 1, "amount": 100}]' in out


def test_prepare_tool_is_idempotent():
    tool = _fake_tool()
    mcp._prepare_mcp_tool(tool, "mysql", "MySQL 数据库")
    wrapped = tool.coroutine

    mcp._prepare_mcp_tool(tool, "mysql", "MySQL 数据库")
    assert tool.coroutine is wrapped
    assert tool.description.count("[连接器：MySQL 数据库]") == 1


def test_collect_mcp_tool_sources_ignores_non_mcp_tools():
    mcp_tool = SimpleNamespace(
        name="query_orders",
        metadata={"mcp_server": "mysql", "mcp_display_name": "MySQL 数据库"},
    )
    local_tool = SimpleNamespace(name="web_search", metadata=None)

    sources = mcp.collect_mcp_tool_sources([mcp_tool, local_tool])
    assert sources == {
        "query_orders": {
            "server": "mysql",
            "display_name": "MySQL 数据库",
            "tool": "query_orders",
        }
    }


# ── 3. 流式事件透传来源 ────────────────────────────────────────────────


def test_stream_processor_marks_tool_events_and_blocks_with_source():
    proc = StreamProcessor(
        mcp_sources={
            "query_orders": {
                "server": "mysql",
                "display_name": "MySQL 数据库",
                "tool": "query_orders",
            }
        }
    )
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "query_orders", "args": {"sql": "select 1"}, "id": "call-1"}],
    )
    events = proc.handle("updates", {"model": {"messages": [ai]}})
    call_ev = next(e for e in events if e["type"] == "tool_call")
    assert call_ev["source"]["display_name"] == "MySQL 数据库"

    result_events = proc.handle(
        "updates",
        {
            "tools": {
                "messages": [
                    ToolMessage(content="1", tool_call_id="call-1", name="query_orders")
                ]
            }
        },
    )
    res_ev = next(e for e in result_events if e["type"] == "tool_result")
    assert res_ev["source"]["server"] == "mysql"

    done = proc.finalize(session_id="s1", elapsed_time=1.0)[0]
    tool_block = next(b for b in done["blocks"] if b["type"] == "tool_call")
    assert tool_block["source"]["tool"] == "query_orders"


def test_stream_processor_non_mcp_tool_has_no_source():
    proc = StreamProcessor()
    ai = AIMessage(
        content="", tool_calls=[{"name": "web_search", "args": {}, "id": "call-2"}]
    )
    events = proc.handle("updates", {"model": {"messages": [ai]}})
    call_ev = next(e for e in events if e["type"] == "tool_call")
    assert "source" not in call_ev
