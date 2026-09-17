#!/usr/bin/env python3
"""MySQL MCP 连通性测试脚本：抓取最近一个月贵金属（黄金/白银）行情写入 MySQL。

用途：验证 mysql MCP server 是否可用。全程通过 langchain-mcp-adapters
连接 mysql MCP（stdio 传输），调用它的 SQL 工具完成【建表 → 写入 → 查询】，
不直接使用 pymysql 等驱动。

行情数据源：STOOQ 免费 CSV（黄金 XAUUSD / 白银 XAGUSD，无需 API Key）。

用法：
    MYSQL_MCP_DIR=/实际/路径/mysql_mcp_server \
    .venv/bin/python tests/demo_mysql_mcp_gold.py

环境变量（均有默认值）：
    MYSQL_MCP_DIR    mysql MCP server 项目目录（uv --directory 指向的路径）
    MYSQL_HOST       MySQL 主机，默认 localhost
    MYSQL_PORT       MySQL 端口，默认 3306
    MYSQL_USER       MySQL 用户，默认 root
    MYSQL_PASSWORD   MySQL 密码，默认 Test1234
    MYSQL_DATABASE   MySQL 库名，默认 agent
    METAL            贵金属：gold | silver，默认 gold
"""

from __future__ import annotations

import asyncio
import csv
import io
import os
import sys
from datetime import date, timedelta

import requests


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


MCP_DIR = _env("MYSQL_MCP_DIR", "path/to/mysql_mcp_server")
METAL = _env("METAL", "gold").strip().lower()
SYMBOL = "XAUUSD" if METAL == "gold" else "XAGUSD"

MYSQL_ENV = {
    "MYSQL_HOST": _env("MYSQL_HOST", "localhost"),
    "MYSQL_PORT": _env("MYSQL_PORT", "3306"),
    "MYSQL_USER": _env("MYSQL_USER", "root"),
    "MYSQL_PASSWORD": _env("MYSQL_PASSWORD", "Test1234"),
    "MYSQL_DATABASE": _env("MYSQL_DATABASE", "agent"),
}

TABLE = "precious_metal_prices"


def fetch_stooq(symbol: str, days: int = 35) -> list[dict]:
    """从 STOOQ 抓取最近 N 天贵金属日线（CSV 免费接口）。"""
    end = date.today()
    start = end - timedelta(days=days)
    url = (
        f"https://stooq.com/q/d/l/?s={symbol}"
        f"&d1={start:%Y%m%d}&d2={end:%Y%m%d}&i=d"
    )
    print(f"[行情] 请求 {symbol} 日线: {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or "No data" in text:
        raise RuntimeError(f"STOOQ 未返回 {symbol} 数据: {text[:120]}")

    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        try:
            rows.append({
                "date": row["Date"],
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(float(row["Volume"])) if row.get("Volume") else None,
            })
        except (ValueError, TypeError, KeyError):
            continue
    rows.sort(key=lambda r: r["date"])
    if not rows:
        raise RuntimeError(f"STOOQ 返回数据解析为空: {text[:120]}")
    print(f"[行情] 获取 {len(rows)} 条记录 | 最近: {rows[-1]}")
    return rows


def _pick_sql_tool(tools: list) -> object:
    """在 MCP 工具中挑一个 SQL 执行工具。"""
    keywords = ("sql", "query", "execute", "statement")
    for t in tools:
        name = getattr(t, "name", "") or ""
        if any(k in name.lower() for k in keywords):
            return t
    return None


async def run_sql(sql_tool: object, statement: str) -> str:
    """调用 MCP SQL 工具，兼容不同参数名（query/sql/command/statement）。"""
    import inspect

    schema = {}
    try:
        schema = getattr(sql_tool, "args_schema", None)
        if schema is not None:
            schema = schema.model_json_schema()
    except Exception:
        schema = {}

    # 优先用参数 schema 里的字段名，否则尝试常见命名
    candidates = ["query", "sql", "command", "statement", "query_str"]
    if isinstance(schema, dict) and schema.get("properties"):
        candidates = list(schema["properties"].keys()) + candidates

    last_err: Exception | None = None
    for key in dict.fromkeys(candidates):
        try:
            result = await sql_tool.ainvoke({key: statement})
            return str(result)
        except TypeError as e:
            last_err = e
        except Exception as e:
            # 参数名正确但 SQL 执行失败：直接抛给上层
            raise RuntimeError(f"SQL 执行失败: {e}") from e
    raise RuntimeError(
        f"无法调用 MCP SQL 工具（参数名不匹配），已尝试: {candidates}，最后错误: {last_err}"
    )


async def main() -> int:
    rows = fetch_stooq(SYMBOL)

    if MCP_DIR == "path/to/mysql_mcp_server" or not os.path.isdir(MCP_DIR):
        print(
            f"[MCP] ❌ MYSQL_MCP_DIR 目录不存在: {MCP_DIR}\n"
            f"      请设置环境变量 MYSQL_MCP_DIR 指向 mysql_mcp_server 项目实际目录，例如：\n"
            f"      MYSQL_MCP_DIR=/home/user/mysql_mcp_server .venv/bin/python tests/demo_mysql_mcp_gold.py",
            file=sys.stderr,
        )
        return 1

    mcp_cfg = {
        "mysql": {
            "command": "uv",
            "args": ["--directory", MCP_DIR, "run", "mysql_mcp_server"],
            "env": MYSQL_ENV,
            "transport": "stdio",
        }
    }

    from langchain_mcp_adapters.client import MultiServerMCPClient

    print("[MCP] 启动 mysql MCP (stdio) ...")
    try:
        client = MultiServerMCPClient(mcp_cfg)
        tools = await client.get_tools()
    except Exception as e:
        print(f"[MCP] ❌ 连接 mysql MCP 失败: {e}", file=sys.stderr)
        return 2

    names = [getattr(t, "name", "?") for t in tools]
    print(f"[MCP] ✅ 已加载 {len(tools)} 个工具: {names}")
    sql_tool = _pick_sql_tool(tools)
    if sql_tool is None:
        print("[MCP] ❌ 未找到 SQL 执行工具", file=sys.stderr)
        return 3
    print(f"[MCP] 使用工具: {getattr(sql_tool, 'name', '?')}")
    try:
        schema = getattr(sql_tool, "args_schema", None)
        if schema is not None:
            props = schema.model_json_schema().get("properties", {})
            print(f"[MCP] 工具参数 schema: {list(props.keys())}")
    except Exception:
        pass

    # 1) 建表
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        date DATE NOT NULL,
        metal VARCHAR(16) NOT NULL DEFAULT '{METAL}',
        open DECIMAL(12,4),
        high DECIMAL(12,4),
        low DECIMAL(12,4),
        close DECIMAL(12,4),
        volume BIGINT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (date, metal)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """
    print(f"[SQL] 建表 {TABLE} ...")
    print(await run_sql(sql_tool, ddl))

    # 2) 写入（幂等：重复日期覆盖）
    values = ", ".join(
        f"('{r['date']}', '{METAL}', {r['open']}, {r['high']}, {r['low']}, "
        f"{r['close']}, {r['volume'] if r['volume'] is not None else 'NULL'})"
        for r in rows
    )
    upsert = f"""
    INSERT INTO {TABLE} (date, metal, open, high, low, close, volume)
    VALUES {values}
    ON DUPLICATE KEY UPDATE
        open = VALUES(open), high = VALUES(high), low = VALUES(low),
        close = VALUES(close), volume = VALUES(volume)
    """
    print(f"[SQL] 写入 {len(rows)} 条记录 ...")
    print(await run_sql(sql_tool, upsert))

    # 3) 查询验证
    count_sql = f"SELECT COUNT(*) AS cnt, MAX(date) AS latest FROM {TABLE} WHERE metal='{METAL}'"
    print(f"[SQL] 验证查询 ...")
    print(await run_sql(sql_tool, count_sql))
    sample_sql = (
        f"SELECT date, metal, open, high, low, close, volume "
        f"FROM {TABLE} WHERE metal='{METAL}' ORDER BY date DESC LIMIT 5"
    )
    print(f"[SQL] 最近 5 条数据 ...")
    print(await run_sql(sql_tool, sample_sql))

    print(f"\n✅ MySQL MCP 测试完成：{METAL.upper()} 近一个月行情已写入库 {MYSQL_ENV['MYSQL_DATABASE']}.{TABLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
