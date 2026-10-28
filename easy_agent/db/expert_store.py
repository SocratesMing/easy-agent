"""专家 Agent 的持久化层（``expert_agents`` 表）。

设计约束（见 ``docs/expert-agent-solution.md`` §3、§7.1）：

- **只接收 ``Database`` 实例**，复用其 ``get_connection()`` / ``_execute()``，
  不往 ``database.py`` 的 2000+ 行里塞业务方法；
- 列表型字段（``export_skills_names`` / ``export_features_prompt``）以 JSON 字符串
  落库，读写时做 ``dumps`` / ``loads``，规避 MySQL 原生 JSON 类型的版本差异；
- 删除默认为**软删除**（``agent_status='disabled'``），物理删除需显式传 ``hard=True``；
- **不加外键约束**，只建索引（与本项目其余表一致）；
- 归属校验统一走 ``user_id``（= ``users.user_id``），越权一律按「不存在」处理。
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Optional

from easy_agent.db.database import Database

logger = logging.getLogger(__name__)

# ── 状态常量 ──────────────────────────────────────────────────────────
STATUS_BUILDING = "building"   # 已落占位行，LLM 生成 / 落目录中
STATUS_ACTIVE = "active"       # 可被选中执行
STATUS_FAILED = "failed"       # 生成失败，前端可重试
STATUS_DISABLED = "disabled"   # 软删除

# 落库时以 JSON 字符串存储的列表型字段
_JSON_FIELDS = ("export_skills_names", "export_required_skills", "export_features_prompt")

# 允许被 update_fields 更新的列（白名单，避免 SQL 注入与误更新主键）
_UPDATABLE_COLUMNS = frozenset({
    "session_id",
    "tag_id",
    "export_name_en",
    "export_name_cn",
    "export_prompt",
    "export_description",
    "export_skills_names",
    "export_required_skills",
    "export_features_prompt",
    "export_file_path",
    "agent_status",
    "export_version",
    "update_timestamp",
})

# 对外暴露的完整列（顺序与建表一致）
COLUMNS = (
    "export_agent_id",
    "session_id",
    "user_id",
    "tag_id",
    "export_name_en",
    "export_name_cn",
    "export_prompt",
    "export_description",
    "export_skills_names",
    "export_required_skills",
    "export_features_prompt",
    "export_file_path",
    "agent_status",
    "export_version",
    "create_timestamp",
    "update_timestamp",
)


def now_iso() -> str:
    """统一的时间戳口径：ISO 字符串（与 sessions.created_at 一致）。"""
    return datetime.now().isoformat(timespec="seconds")


_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name_cn(value: Any) -> str:
    """专家中文名的比较口径：忽略全部空白（含全角空格）+ 忽略大小写。

    用户手工输入时很容易多打一个空格或大小写不一致，若按原样比较就会漏判，
    出现「看起来一模一样」的两条记录（正是本次要修的 Bug）。
    """
    return _WHITESPACE_RE.sub("", str(value or "")).casefold()


def _row_to_dict(row: Any) -> dict:
    """把 SQLite ``sqlite3.Row`` / MySQL ``DictCursor`` 的行统一成 dict。

    同时把 JSON 字段解码为 Python 列表；解码失败时降级为空列表（不抛异常，
    避免一条脏数据把整个列表接口打挂）。
    """
    data = dict(row)
    for field in _JSON_FIELDS:
        raw = data.get(field)
        if isinstance(raw, (list, dict)):
            continue
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                data[field] = parsed if isinstance(parsed, list) else []
            except (ValueError, TypeError):
                logger.warning("专家记录字段 %s 不是合法 JSON，已降级为空列表", field)
                data[field] = []
        else:
            data[field] = []
    return data


def _encode(fields: dict) -> dict:
    """写入前把列表型字段编码成 JSON 字符串。"""
    encoded = dict(fields)
    for field in _JSON_FIELDS:
        if field in encoded and not isinstance(encoded[field], (str, type(None))):
            encoded[field] = json.dumps(encoded[field], ensure_ascii=False)
    return encoded


class ExpertStore:
    """``expert_agents`` 表的 CRUD。

    用法::

        store = ExpertStore(db)
        store.create({...})
        record = store.get_owned(expert_id, user_id)
    """

    def __init__(self, db: Database):
        self.db = db

    # ── 写 ────────────────────────────────────────────────────────────

    def create(self, fields: dict) -> None:
        """插入一条专家记录（缺失列使用建表默认值）。

        ``fields`` 里的 ``export_skills_names`` / ``export_features_prompt`` 可传
        Python 列表，落库前会自动编码为 JSON 字符串。
        """
        payload = _encode(fields)
        now = now_iso()
        payload.setdefault("session_id", "")
        payload.setdefault("tag_id", "")
        payload.setdefault("export_prompt", "")
        payload.setdefault("export_description", "")
        payload.setdefault("export_file_path", "")
        payload.setdefault("agent_status", STATUS_BUILDING)
        payload.setdefault("export_version", 1)
        payload["create_timestamp"] = payload.get("create_timestamp") or now
        payload["update_timestamp"] = payload.get("update_timestamp") or now

        cols = [c for c in COLUMNS if c in payload]
        placeholders = ", ".join("?" for _ in cols)
        sql = (
            f"INSERT INTO expert_agents ({', '.join(cols)}) "
            f"VALUES ({placeholders})"
        )
        with self.db.get_connection() as conn:
            self.db._execute(conn.cursor(), sql, tuple(payload[c] for c in cols))

    def update_fields(self, expert_agent_id: str, fields: dict) -> None:
        """按白名单更新指定列，并自动刷新 ``update_timestamp``。"""
        payload = _encode(fields)
        payload = {k: v for k, v in payload.items() if k in _UPDATABLE_COLUMNS}
        if not payload:
            return
        payload["update_timestamp"] = now_iso()
        assignments = ", ".join(f"{col}=?" for col in payload)
        sql = f"UPDATE expert_agents SET {assignments} WHERE export_agent_id=?"
        with self.db.get_connection() as conn:
            self.db._execute(
                conn.cursor(), sql, (*payload.values(), expert_agent_id)
            )

    def list_by_session(self, user_id: str, session_id: str) -> list[dict]:
        """列出某个会话里创建的专家（工作区「专家设定 MD」只读条目的数据源）。

        专家记录在创建时就带 ``session_id``（溯源用），这里按它反查，用于在
        创建该专家的会话工作区里展示其提示词 MD。已软删（disabled）的不返回。
        """
        if not session_id:
            return []
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM expert_agents "
                "WHERE user_id=? AND session_id=? AND agent_status<>? "
                "ORDER BY create_timestamp ASC",
                (user_id, session_id, STATUS_DISABLED),
            )
            rows = cursor.fetchall()
        return [_row_to_dict(r) for r in rows]

    def delete(self, expert_agent_id: str, user_id: str, hard: bool = False) -> bool:
        """删除专家。``hard=False`` 为软删除（置 ``disabled``）。

        Returns:
            True 表示确实命中了本用户的记录。
        """
        if hard:
            sql = (
                "DELETE FROM expert_agents "
                "WHERE export_agent_id=? AND user_id=?"
            )
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                self.db._execute(cursor, sql, (expert_agent_id, user_id))
                return cursor.rowcount > 0

        record = self.get_owned(expert_agent_id, user_id)
        if record is None:
            return False
        self.update_fields(
            expert_agent_id,
            {"agent_status": STATUS_DISABLED},
        )
        return True

    # ── 读 ────────────────────────────────────────────────────────────

    def get(self, expert_agent_id: str) -> Optional[dict]:
        """按主键读取（**不做归属校验**，仅供内部/测试使用）。"""
        if not expert_agent_id:
            return None
        sql = "SELECT * FROM expert_agents WHERE export_agent_id=?"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, (expert_agent_id,))
            row = cursor.fetchone()
        return _row_to_dict(row) if row else None

    def get_owned(self, expert_agent_id: str, user_id: str) -> Optional[dict]:
        """读取并校验归属；越权或不存在的返回 ``None``（由调用方转 404）。"""
        if not expert_agent_id:
            return None
        sql = (
            "SELECT * FROM expert_agents "
            "WHERE export_agent_id=? AND user_id=?"
        )
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, (expert_agent_id, user_id))
            row = cursor.fetchone()
        return _row_to_dict(row) if row else None

    def find_by_name_cn(
        self, user_id: str, name_cn: str, exclude_agent_id: str = ""
    ) -> Optional[dict]:
        """按中文名查找本用户下「未删除」专家中的重名记录（创建/修改时的重名校验）。

        比较口径见 :func:`normalize_name_cn`（忽略空白与大小写）。
        **已删除（disabled）的记录不参与校验**：它们已从列表里移除，名字视为
        可复用——否则「删掉重建同名专家」会被莫名其妙地拦住。

        Args:
            user_id: 归属用户（``users.user_id``）。
            name_cn: 待校验的中文名。
            exclude_agent_id: 需要排除的专家 ID（修改时不与自己比）。

        Returns:
            命中的记录（至少含 ``export_agent_id`` / ``export_name_cn`` /
            ``agent_status``）；无冲突返回 ``None``。
        """
        target = normalize_name_cn(name_cn)
        if not target:
            return None

        sql = (
            "SELECT export_agent_id, export_name_cn, agent_status "
            "FROM expert_agents WHERE user_id=? AND agent_status<>?"
        )
        params: list[Any] = [user_id, STATUS_DISABLED]
        if exclude_agent_id:
            sql += " AND export_agent_id<>?"
            params.append(exclude_agent_id)

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, tuple(params))
            rows = cursor.fetchall()

        for row in rows:
            data = dict(row)
            if normalize_name_cn(data.get("export_name_cn")) == target:
                return data
        return None

    def list_by_user(
        self,
        user_id: str,
        keyword: str = "",
        status: str = "",
        include_disabled: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        """按用户分页查询专家列表。

        Args:
            user_id: 归属用户（``users.user_id``）。
            keyword: 对 ``export_name_cn`` / ``export_name_en`` 做 LIKE 模糊匹配。
            status: 指定 ``agent_status``；为空时默认排除 ``disabled``。
            include_disabled: 为 True 时不过滤 ``disabled``（前端「回收站」用）。
            page / page_size: 从 1 开始的页码。

        Returns:
            ``(total, items)``。
        """
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 20), 1), 200)
        offset = (page - 1) * page_size

        where = ["user_id=?"]
        params: list[Any] = [user_id]
        if status:
            where.append("agent_status=?")
            params.append(status)
        elif not include_disabled:
            where.append("agent_status<>?")
            params.append(STATUS_DISABLED)
        if keyword:
            where.append("(export_name_cn LIKE ? OR export_name_en LIKE ?)")
            like = f"%{keyword}%"
            params.extend([like, like])

        where_sql = " AND ".join(where)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"SELECT COUNT(*) AS n FROM expert_agents WHERE {where_sql}",
                tuple(params),
            )
            count_row = cursor.fetchone()
            if count_row is None:
                total = 0
            else:
                total = count_row["n"] if isinstance(count_row, dict) else count_row[0]

            self.db._execute(
                cursor,
                "SELECT * FROM expert_agents "
                f"WHERE {where_sql} "
                "ORDER BY update_timestamp DESC, create_timestamp DESC "
                "LIMIT ? OFFSET ?",
                (*params, page_size, offset),
            )
            rows = cursor.fetchall()

        return int(total or 0), [_row_to_dict(r) for r in rows]
