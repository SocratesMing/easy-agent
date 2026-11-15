"""逐调用 Token 账本：幂等写入及单一读快照中的个人统计。"""

from __future__ import annotations

import time
from datetime import datetime, time as day_time, timedelta, timezone
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


class TokenUsageRepository:
    def __init__(self, db):
        self._db = db

    def record_usage(
        self,
        *,
        event_id: str,
        user_id: str,
        session_id: str | None,
        model_key: str,
        model_name: str,
        completed_at_ms: int,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        for name, value in (
            ("completed_at_ms", completed_at_ms),
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
        ):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name, value, limit in (
            ("event_id", event_id, 64),
            ("user_id", user_id, 255),
            ("model_key", model_key, 128),
            ("model_name", model_name, 255),
        ):
            if not isinstance(value, str) or not value or len(value) > limit:
                raise ValueError(f"invalid {name}")
        usage_date = datetime.fromtimestamp(completed_at_ms / 1000, _SHANGHAI).date().isoformat()
        conflict = (
            "ON CONFLICT(event_id) DO NOTHING"
            if self._db.db_type == "sqlite"
            else "ON DUPLICATE KEY UPDATE event_id=event_id"
        )
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                self._db._execute(
                    cursor,
                    f"""
                    INSERT INTO token_usage
                        (event_id, user_id, session_id, model_key, model_name,
                         completed_at_ms, usage_date, input_tokens, output_tokens)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) {conflict}
                """,
                    (
                        event_id,
                        user_id,
                        session_id,
                        model_key,
                        model_name,
                        completed_at_ms,
                        usage_date,
                        input_tokens,
                        output_tokens,
                    ),
                )
            finally:
                cursor.close()

    def session_totals(self, username: str, session_id: str) -> dict:
        """All recorded calls for this user's conversation, across turns/models."""
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                self._db._execute(
                    cursor,
                    """
                    SELECT COALESCE(SUM(t.input_tokens), 0) AS input_tokens,
                           COALESCE(SUM(t.output_tokens), 0) AS output_tokens
                    FROM token_usage t JOIN users u ON u.user_id = t.user_id
                    WHERE u.username = ? AND t.session_id = ?
                    """,
                    (username, session_id),
                )
                return {key: int(value) for key, value in dict(cursor.fetchone()).items()}
            finally:
                cursor.close()

    def query(self, user_id: str, days: int = 7, as_of_ms: int | None = None) -> dict:
        if type(days) is not int or days not in (7, 30):
            raise ValueError("days must be 7 or 30")
        if not user_id:
            raise ValueError("user_id is required")
        as_of_ms = time.time_ns() // 1_000_000 if as_of_ms is None else as_of_ms
        as_of = datetime.fromtimestamp(as_of_ms / 1000, timezone.utc)
        today = as_of.astimezone(_SHANGHAI).date()

        def midnight(offset):
            return int(
                datetime.combine(today - timedelta(days=offset), day_time(), _SHANGHAI).timestamp()
                * 1000
            )

        dates = [(today - timedelta(days=offset)).isoformat() for offset in reversed(range(days))]
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            try:
                if self._db.db_type == "sqlite":
                    cursor.execute("BEGIN")
                else:
                    # 仅当前事务覆盖服务器默认值，不污染下次池借用者的配置。
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    cursor.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                self._db._execute(
                    cursor,
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN completed_at_ms >= ? THEN input_tokens + output_tokens ELSE 0 END), 0) AS today,
                        COALESCE(SUM(CASE WHEN completed_at_ms >= ? THEN input_tokens + output_tokens ELSE 0 END), 0) AS last_7_days,
                        COALESCE(SUM(CASE WHEN completed_at_ms >= ? THEN input_tokens + output_tokens ELSE 0 END), 0) AS last_30_days,
                        COALESCE(SUM(input_tokens + output_tokens), 0) AS all_time
                    FROM token_usage WHERE user_id = ? AND completed_at_ms < ?
                """,
                    (midnight(0), midnight(6), midnight(29), user_id, as_of_ms),
                )
                summary = {key: int(value) for key, value in dict(cursor.fetchone()).items()}
                self._db._execute(
                    cursor,
                    """
                    SELECT usage_date, model_key, model_name,
                           SUM(input_tokens + output_tokens) AS total
                    FROM token_usage
                    WHERE user_id = ? AND completed_at_ms >= ? AND completed_at_ms < ?
                    GROUP BY usage_date, model_key, model_name
                """,
                    (user_id, midnight(days - 1), as_of_ms),
                )
                rows = cursor.fetchall()
            finally:
                cursor.close()

        indices = {date: index for index, date in enumerate(dates)}
        models = {}
        for row in rows:
            identity = (row["model_key"], row["model_name"])
            values = models.setdefault(identity, [0] * days)
            values[indices[row["usage_date"]]] = int(row["total"])
        series = [
            dict(model_key=key, model_name=name, values=values)
            for (key, name), values in models.items()
            if sum(values) > 0
        ]
        series.sort(key=lambda item: (-sum(item["values"]), item["model_key"], item["model_name"]))
        return {
            "timezone": "Asia/Shanghai",
            "as_of": as_of.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "days": days,
            "summary": summary,
            "daily": {
                "dates": dates,
                "totals": [sum(item["values"][index] for item in series) for index in range(days)],
                "series": series,
            },
        }
