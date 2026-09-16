"""端到端冒烟：连真实 mcp-server，用真实 API Key 打真实库。

与 tests/ 下的单测不同——单测用内存假数据验证"代码逻辑"，这里验证
"服务 + 鉴权 + 索引 + 领域查询"整条链路是否真的可用。

两种运行方式：

1. 自启动（默认）：脚本在进程内拉起 uvicorn，用**真实 MySQL 鉴权器**
   （Key → sha256 → 查 mcp_api_keys → username），跑完自动关闭。

       uv run python -m scripts.smoke_dataqa --key <dataqa 的 API Key>

2. 连外部已运行的服务：

       uv run python -m scripts.smoke_dataqa --url http://127.0.0.1:8100 --key <key>

安全：Key 只从命令行或环境变量 MCP_DATAQA_KEY 读取，不要写进任何文件。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

DEFAULT_PORT = 8100
BUSINESS = "dataqa"

BOND_KEYS = ("bond_code", "bond_name", "bond_type", "term")
QUOTE_KEYS = (
    "update_time_readable",
    "bond_code",
    "level_no",
    "bid_price",
    "bid_yield",
    "offer_price",
    "offer_yield",
)


async def call_tool(url: str, key: str, tool: str, args: dict | None = None):
    async with streamablehttp_client(
        f"{url}/mcp/{BUSINESS}/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args or {})


def _payload(result) -> dict | str:
    text = result.content[0].text if result.content else ""
    try:
        return json.loads(text)
    except Exception:
        return text


def _brief(title: str, payload, keys: tuple[str, ...] = (), sample: int = 3) -> None:
    print(f"\n--- {title} ---")
    if isinstance(payload, str):
        print(payload[:500])
        return
    if payload.get("ok") is False:
        print("FAILED:", payload.get("error"))
        return

    rows = payload.get("rows")
    if isinstance(rows, list):
        print(f"rows: {len(rows)}")
        for row in rows[:sample]:
            print("   ", {k: row.get(k) for k in keys} if keys else row)
        if payload.get("summary"):
            print("summary:", payload["summary"])
    else:
        text = json.dumps(payload, ensure_ascii=False)
        print(text[:500])


async def _start_embedded(port: int):
    """在进程内拉起服务，用真实 MySQL 鉴权器（不是测试用的假 verifier）。"""
    from easy_mcp_server.app import create_app
    from easy_mcp_server.keys import create_mysql_verifier

    app = create_app(create_mysql_verifier())
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    return server, task


async def main_async(url: str | None, port: int, key: str) -> None:
    server = task = None
    if not url:
        server, task = await _start_embedded(port)
        url = f"http://127.0.0.1:{port}"

    print(f"target: {url}/mcp/{BUSINESS}/")
    try:
        await _run_checks(url, key)
    finally:
        if server is not None:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)


async def _run_checks(url: str, key: str) -> None:
    _brief(
        "list_bonds(days=30)",
        _payload(await call_tool(url, key, "list_bonds", {"days": 30})),
        BOND_KEYS,
    )

    _brief(
        "get_bond_quotes(days=30, daily, level=1)  ← 主场景",
        _payload(
            await call_tool(
                url,
                key,
                "get_bond_quotes",
                {"days": 30, "granularity": "daily", "level": 1},
            )
        ),
        QUOTE_KEYS,
    )

    _brief(
        "get_bond_quotes(bond_codes=['2105005'], latest)  ← 裸代码无后缀",
        _payload(
            await call_tool(
                url,
                key,
                "get_bond_quotes",
                {"bond_codes": ["2105005"], "granularity": "latest", "days": 30},
            )
        ),
        QUOTE_KEYS,
    )

    _brief(
        "get_bond_quotes(granularity='raw', limit=5)  ← 明细",
        _payload(
            await call_tool(
                url,
                key,
                "get_bond_quotes",
                {"granularity": "raw", "days": 7, "limit": 5},
            )
        ),
        QUOTE_KEYS,
        sample=5,
    )

    _brief(
        "describe_xbond_schema()",
        _payload(await call_tool(url, key, "describe_xbond_schema")),
    )

    _brief(
        "query(自定义 SQL)  ← 通用逃生口",
        _payload(
            await call_tool(
                url,
                key,
                "query",
                {
                    "sql": (
                        "SELECT bond_code, bond_name, MAX(bid_yield) AS max_yield "
                        "FROM v_xbond_depth WHERE level_no = 1 "
                        "GROUP BY bond_code, bond_name ORDER BY max_yield DESC"
                    )
                },
            )
        ),
    )

    print("\n--- 负向：错误 Key ---")
    try:
        await call_tool(url, "mcp_invalid_key_for_smoke", "list_bonds", {})
        print("!!! 错误 Key 竟然通过了，鉴权有问题")
    except Exception as e:
        print(f"被拒绝: {type(e).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("MCP_SMOKE_URL", ""),
        help="外部服务地址；不给则进程内自启动",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="自启动端口")
    parser.add_argument(
        "--key",
        default=os.environ.get("MCP_DATAQA_KEY", ""),
        help="dataqa 业务的 API Key（也可设环境变量 MCP_DATAQA_KEY）",
    )
    args = parser.parse_args()
    if not args.key:
        parser.error("缺少 --key，或设置环境变量 MCP_DATAQA_KEY")

    asyncio.run(main_async(args.url.rstrip("/") or None, args.port, args.key))


if __name__ == "__main__":
    main()
