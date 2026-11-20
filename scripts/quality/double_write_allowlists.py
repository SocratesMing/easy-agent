"""Frozen allowlists for scripts/quality/check_double_write.py.

Keep this module constant-only (no imports from the checker) so the CI gate can
be reviewed by diffing a small, auditable allowlist file.
"""

from __future__ import annotations

# --- Database freeze allowlist ------------------------------------------------
# Infrastructure: connection, transaction, DDL/migration plumbing and the
# process health probe.  These may contain direct SQL.
INFRA_DATABASE_METHODS = {
    "__init__",
    "_init_mysql_pool",
    "_get_sqlite_connection",
    "_get_mysql_connection",
    "_get_connection",
    "close",
    "get_connection",
    "_execute",
    "_create_index",
    "_create_sessions_table",
    "_create_session_messages_table",
    "_create_files_tables",
    "_create_users_table",
    "_create_misc_tables",
    "_create_scheduled_task_tables",
    "_create_session_runtime_table",
    "_create_langgraph_store_table",
    "_create_distributed_lock_table",
    "_create_stream_events_table",
    "_create_agent_runs_table",
    "_create_expert_tables",
    "_create_unique_index",
    "init_tables",
    "verify_schema_current",
    "_init_tables_full",
    "_ensure_schema_meta_table",
    "_store_schema_version",
    "_schema_tables_present",
    "_repair_missing_message_rows",
    "_find_mismatched_sessions",
    "_ensure_column",
    "_table_columns",
    "_column_type",
    "_ensure_unique_index",
    "ping",
}

# T3 各批删除的 Database 纯委托不再逐个登记：方法不在下面的
# FROZEN_DATABASE_METHODS 里就会被门禁拦下，纯委托数量由 MAX_DATABASE_PURE_DELEGATIONS 约束。
# Frozen set of every method that exists on Database at the time phase 0 lands
# (ARCH-03 phase 2 extracted the SQL into db/repositories/*).  Non-infrastructure
# entries must stay pure delegations; see _is_pure_delegation.
FROZEN_DATABASE_METHODS = {
    "__init__",
    "_init_mysql_pool",
    "_get_sqlite_connection",
    "_get_mysql_connection",
    "_get_connection",
    "close",
    "get_connection",
    "_execute",
    "_create_index",
    "_create_sessions_table",
    "_create_session_messages_table",
    "_create_files_tables",
    "_create_users_table",
    "_create_misc_tables",
    "_create_scheduled_task_tables",
    "_create_session_runtime_table",
    "_create_langgraph_store_table",
    "_create_distributed_lock_table",
    "_create_stream_events_table",
    "_create_agent_runs_table",
    "init_tables",
    "verify_schema_current",
    "_init_tables_full",
    "_create_expert_tables",
    "_ensure_schema_meta_table",
    "_store_schema_version",
    "_schema_tables_present",
    "_repair_missing_message_rows",
    "_find_mismatched_sessions",
    "_ensure_column",
    "_table_columns",
    "_column_type",
    "_ensure_unique_index",
    "_create_unique_index",
    "create_session",
    "get_session",
    "update_session_workspace_name",
    "update_last_assistant_message",
    "create_user",
    "get_user_by_username",
    "get_user_by_id",
    "increment_user_token_version",
    "register_user",
    "verify_user_password",
    "get_or_create_passwordless_user",
    "update_user_password",
    "ping",
}


# --- T3 数据访问层收敛：只减不增的计数上限 --------------------------------
# 每次按领域收敛（T3a 会话/消息域 → T3b 文件/用户域 → T3c 其余域）都只允许
# **下调**这些上限；新增调用方请改用领域 repository（服务层从
# ``easy_agent/db/providers.py`` 取，API 层走 ``easy_agent/api/deps.py``）。
#
# 口径（scanner 用 AST 统计，不含注释 / 字符串 / import 行）：
# - ``MAX_DATABASE_METHODS``：``easy_agent/db/database.py`` 里 class Database 的方法总数；
# - ``MAX_DATABASE_PURE_DELEGATIONS``：其中纯委托方法数（只转调 db/repositories 与
#   db/schema 的单行方法）；
# - ``MAX_GET_DATABASE_CALLS_OUTSIDE_DB``：``easy_agent/`` 中除 ``easy_agent/db/`` 外
#   显式调用 ``get_database()`` 的次数；
# - ``MAX_DATABASE_ESCAPE_HATCH_REFS``：``easy_agent/`` 中除 ``easy_agent/db/`` 外
#   ``repo.database`` / ``repository.database`` / ``db.database`` 属性访问次数
#   （``Database`` 逃生口）。
#
# T3c（2026-11-01 定时任务/专家/agent_state/流运行时等收尾）实测：47 / 12 / 4 / 5。
# 保留的 12 个纯委托全部是 knowledges / personnel 纵向切片仍在直连的
# sessions 4 个 + users 8 个（见下方 T3c 说明）；其余为连接/事务/迁移。
MAX_DATABASE_METHODS = 47
MAX_DATABASE_PURE_DELEGATIONS = 12
MAX_GET_DATABASE_CALLS_OUTSIDE_DB = 4
MAX_DATABASE_ESCAPE_HATCH_REFS = 5
MAX_DATABASE_ESCAPE_HATCH_REFS = 12
