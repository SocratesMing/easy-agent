"""「数据查询止损」离线验证脚手架（假行情 MCP server + 剧本跑批）。

用途：不接真实行情源、不接大模型，验证 ``ToolProgressMiddleware`` 对真实
MCP 信封的行为。结果与线上一致的原因：这里用真正的 ``wrap_mcp_tools`` 包装
真正的 FastMCP 工具，``content`` 的类型/结构与 ToolNode 产出的 ToolMessage 相同。

用法：

    # 1) 只跑剧本（会自动以 stdio 拉起本文件的 --serve 分支当 MCP server）
    python tests/fixtures/mcp_stop_loss_probe.py

    # 2) 手工当 server 起，供前端联调：把下面这段加进 mcp.json 的 mcpServers
    #    "fake-market": {
    #      "command": "<venv>/python.exe",
    #      "args": ["<repo>/tests/fixtures/mcp_stop_loss_probe.py", "--serve"],
    #      "transport": "stdio"
    #    }
    python tests/fixtures/mcp_stop_loss_probe.py --serve

剧本与期望：

    A 同一空目标连查 4 次      → 第 3 次起出现「⛔ … 止损上限」
    B 分页 30 页（每页不同）    → 0 次注入（分页不能误伤）
    C 同一状态轮询 13 次        → 第 12 次起出现收敛/停止提示（当前会被误杀，见评审）
    D AU 有数据后 PT 连查 6 次  → 当前只有「正常遍历」提示，不升级（缺陷）
"""

from __future__ import annotations

import asyncio
import sys


# ---------------------------------------------------------------------------
# 假 MCP server（--serve 分支）
# ---------------------------------------------------------------------------


def _serve() -> None:  # pragma: no cover - 子进程分支
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("fake-market")

    @server.tool()
    def query_market(symbol: str, start: str = "2024-01-01", end: str = "2024-02-01") -> str:
        """查询某标的一段区间行情。AU / AG 有数据，其它标的返回空。"""
        if symbol.upper() in {"AU", "AG"}:
            return (
                '{"code":0,"total":2,"data":['
                '{"date":"2024-01-02","close":1.5},'
                '{"date":"2024-01-03","close":1.6}]}'
            )
        return '{"code":0,"total":0,"data":[]}'

    @server.tool()
    def page_data(page: int = 1) -> str:
        """分页取数：每页内容不同（验证分页不会被误判成「无进展」）。"""
        return (
            '{"code":0,"row_count":2,"data":['
            '{"page":%d,"v":%d},{"page":%d,"v":%d}]}' % (page, page * 100, page, page * 100 + 1)
        )

    @server.tool()
    def job_status(job_id: str) -> str:
        """任务状态轮询：同一状态会重复返回（验证轮询场景会不会被误杀）。"""
        return '{"status":"running","progress":37}'

    server.run()


# ---------------------------------------------------------------------------
# 剧本跑批
# ---------------------------------------------------------------------------


async def _main() -> None:  # pragma: no cover - 手工联调脚本
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_mcp_adapters.client import MultiServerMCPClient

    import easy_agent.agent.tool_progress as tp
    from easy_agent.services.mcp.evidence import wrap_mcp_tools

    client = MultiServerMCPClient(
        {
            "fake": {
                "command": sys.executable,
                "args": [__file__, "--serve"],
                "transport": "stdio",
                "env": {},
            }
        }
    )
    tools = {t.name: t for t in wrap_mcp_tools({"fake": await client.get_tools()})}
    middleware = tp.ToolProgressMiddleware()

    async def call(tool: str, args: dict, history: list, call_id: str) -> str:
        raw = await tools[tool].ainvoke(args)
        # ToolNode 对 (content, artifact) 会拆包，content 即第一项
        content = raw[0] if isinstance(raw, tuple) else raw
        message = ToolMessage(content=content, tool_call_id=call_id, name=tool)
        request = SimpleNamespace(
            tool_call={"name": tool, "args": args, "id": call_id},
            state={"messages": list(history)},
        )
        annotated = middleware.annotate(request, message)
        history.append(
            AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": call_id}])
        )
        history.append(annotated)
        return tp._content_text(annotated)

    def note_of(text: str) -> str:
        return text.split(tp.NOTE_MARK)[-1].strip()[:38] if tp.NOTE_MARK in text else "（无提示）"

    print("== A 空目标连查 4 次 ==")
    history: list = []
    for index in range(4):
        text = await call(
            "query_market", {"symbol": "XX", "start": f"2024-0{index + 1}-01"}, history, f"a{index}"
        )
        print(f"  第{index + 1}次: {note_of(text)}")

    print("== B 分页 30 页 ==")
    history = []
    injected = 0
    for page in range(1, 31):
        text = await call("page_data", {"page": page}, history, f"b{page}")
        injected += tp.NOTE_MARK in text
    print(f"  注入 {injected} 次（期望 0）")

    print("== C 同一状态轮询 13 次 ==")
    history = []
    for index in range(13):
        text = await call("job_status", {"job_id": "J1"}, history, f"c{index}")
        if index >= 10:
            print(f"  第{index + 1}次: {note_of(text)}")

    print("== D AU 有数据后 PT 连查 10 次（硬顶 9 = 3×3）==")
    history = []
    await call("query_market", {"symbol": "AU"}, history, "d0")
    for index in range(10):
        text = await call(
            "query_market", {"symbol": "PT", "start": f"2024-0{index % 9 + 1}-01"}, history, f"d{index + 1}"
        )
        if index >= 7:
            print(f"  第{index + 1}次空: {note_of(text)}")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _serve()
    else:
        asyncio.run(_main())
