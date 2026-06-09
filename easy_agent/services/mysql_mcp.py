"""Local MySQL MCP server.

Replaces the external mysql-mcp-server package with a robust local
implementation that properly handles multi-statement queries.
"""

import asyncio
import logging
import os
import sys

from mysql.connector import connect, Error
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("mysql_mcp_server")


def get_db_config() -> dict:
    config = {
        "host": os.getenv("MYSQL_HOST", "localhost"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER"),
        "password": os.getenv("MYSQL_PASSWORD"),
        "database": os.getenv("MYSQL_DATABASE"),
        "charset": "utf8mb4",
        "use_unicode": True,
        "autocommit": False,
        "get_warnings": True,
        "raise_on_warnings": False,
        "connection_timeout": 10,
    }
    missing = [k for k in ("user", "password", "database") if not config.get(k)]
    if missing:
        msg = f"Missing required env vars: {', '.join(f'MYSQL_{k.upper()}' for k in missing)}"
        raise ValueError(msg)
    return config


def _execute_single(cursor, stmt: str) -> list[list[str]]:
    """Execute a single SQL statement and return results as a table (list of rows)."""
    stmt = stmt.strip()
    if not stmt:
        return []

    cursor.execute(stmt)

    # Check if this statement produces result sets (SELECT, SHOW, DESCRIBE, EXPLAIN)
    if cursor.description is not None:
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        table = [columns]
        for row in rows:
            table.append([str(v) if v is not None else "NULL" for v in row])
        # Consume any remaining result sets from multi-statement
        while cursor.nextset():
            pass
        return table

    # DDL / DML statements (INSERT, UPDATE, DELETE, CREATE, USE, etc.)
    conn = cursor._connection
    if conn and conn.in_transaction:
        conn.commit()
    return []


def execute_query(query: str) -> str:
    """Execute one or more SQL statements, return formatted results."""
    config = get_db_config()

    # Split on semicolons to handle multi-statement queries
    raw_statements = [s.strip() for s in query.split(";") if s.strip()]

    if not raw_statements:
        return "Error: empty query"

    parts: list[str] = []

    with connect(**config) as conn:
        conn.autocommit = False
        for i, stmt in enumerate(raw_statements):
            try:
                with conn.cursor() as cursor:
                    table = _execute_single(cursor, stmt)
                    if table:
                        header = " | ".join(table[0])
                        sep = "-" * len(header)
                        rows = "\n".join(" | ".join(row) for row in table[1:])
                        block = (
                            f"Result #{i + 1}:\n{header}\n{sep}\n{rows}"
                            if table[1:]
                            else f"Result #{i + 1}:\n{header}\n{sep}\n(empty)"
                        )
                        parts.append(block)
                    else:
                        affected = cursor.rowcount
                        if affected >= 0:
                            parts.append(
                                f"Result #{i + 1}: OK, {affected} row(s) affected"
                            )
                        else:
                            parts.append(f"Result #{i + 1}: OK")
            except Error as e:
                parts.append(f"Result #{i + 1}: Error - {e}")
                conn.rollback()
                break

    return "\n\n".join(parts)


app = Server("mysql_mcp_server")


@app.list_tools()
async def list_tools() -> list[Tool]:
    logger.info("Listing tools...")
    return [
        Tool(
            name="execute_sql",
            description="Execute one or more SQL statements on the MySQL server. Supports SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, SHOW, DESCRIBE, USE, and other SQL statements. Multiple statements separated by semicolons are supported.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL query or queries to execute (multiple statements separated by semicolons are supported)",
                    }
                },
                "required": ["query"],
            },
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "execute_sql":
        raise ValueError(f"Unknown tool: {name}")

    query = arguments.get("query", "")
    logger.info(f"Calling tool: {name} with arguments: {arguments}")

    if not query:
        return [TextContent(type="text", text="Error: query is required")]

    try:
        result = await asyncio.to_thread(execute_query, query)
        return [TextContent(type="text", text=result)]
    except Error as e:
        logger.error(f"Error executing SQL: {e}")
        return [TextContent(type="text", text=f"Error executing query: {e}")]
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return [TextContent(type="text", text=f"Unexpected error: {e}")]


async def main():
    config = get_db_config()
    print(f"Host: {config['host']}", file=sys.stderr)
    print(f"Port: {config['port']}", file=sys.stderr)
    print(f"User: {config['user']}", file=sys.stderr)
    print(f"Database: {config['database']}", file=sys.stderr)

    logger.info(
        f"Starting MySQL MCP server ({config['host']}/{config['database']} as {config['user']})"
    )

    async with stdio_server() as (read_stream, write_stream):
        try:
            await app.run(
                read_stream, write_stream, app.create_initialization_options()
            )
        except Exception as e:
            logger.error(f"Server error: {e}", exc_info=True)
            raise


if __name__ == "__main__":
    asyncio.run(main())
