"""MCP API Key repository（ARCH-03 Phase 2）。"""

from __future__ import annotations

from datetime import datetime


# ── MCP API Key（主应用签发，mcp-server 子项目只读校验） ─────────────

def issue_mcp_api_key(db, username: str, business: str, key_hash: str) -> bool:
    """签发（或重签）某用户某业务的 MCP API Key，明文不落库。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # upsert：sqlite 用 ON CONFLICT，mysql 用 ON DUPLICATE KEY UPDATE
    if db.db_type == "sqlite":
        upsert_sql = """
            INSERT INTO mcp_api_keys (username, business, key_hash, created_at, updated_at, revoked)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(username, business) DO UPDATE SET
                key_hash=excluded.key_hash, updated_at=excluded.updated_at, revoked=0
        """
    else:
        upsert_sql = """
            INSERT INTO mcp_api_keys (username, business, key_hash, created_at, updated_at, revoked)
            VALUES (?, ?, ?, ?, ?, 0)
            ON DUPLICATE KEY UPDATE
                key_hash=VALUES(key_hash), updated_at=VALUES(updated_at), revoked=0
        """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, upsert_sql, (username, business, key_hash, now, now))
    return True

def revoke_mcp_api_key(db, username: str, business: str) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE mcp_api_keys SET revoked=1, updated_at=? WHERE username=? AND business=?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username, business),
        )
        return cursor.rowcount > 0

def get_mcp_api_keys(db, username: str) -> list[dict]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT business, created_at, updated_at, revoked "
            "FROM mcp_api_keys WHERE username=? ORDER BY business",
            (username,),
        )
        rows = cursor.fetchall()
    return [dict(r) if not isinstance(r, dict) else r for r in rows]
