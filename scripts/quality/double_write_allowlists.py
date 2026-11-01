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

# T3a（2026-11-01，会话/消息域）删除的纯委托（已无 db/ 外调用方，见设计文档 T3）：
#   get_session_owner / _get_legacy_session_messages / update_session_meta /
#   list_sessions / update_session / delete_session / update_session_todos /
#   _sanitize_message_for_storage / _sanitize_blocks /
#   _sanitize_tool_calls / _sanitize_extra_data / add_message_row /
#   get_messages_from_rows / get_messages / _try_repair_json / count_sessions /
#   update_session_title / toggle_session_pin / start_run / renew_run /
#   finish_run / get_run / list_stale_running / mark_interrupted /
#   delete_stream_events_before
# 例外：add_message 仍被 services/scheduler.py（定时任务域，未迁移）调用，
#       保留到 T3c 再删。
#
# T3b（2026-11-01，文件/用户域）删除的纯委托：
#   files: add_session_file / get_session_files / delete_file /
#          get_generated_filenames
#   users: get_user_by_employee_id / get_user_by_account / update_user /
#          bind_user_ip / get_user_bound_ip / get_user_token_version /
#          touch_user_activity / get_user_activity_time / clear_user_activity /
#          delete_user / get_or_create_default_user / list_users / count_users /
#          _row_to_user
# 保留说明：文件域 add_generated_file / get_generated_files / delete_generated_file
# 仍被 persistence.py 兼容端口与 streaming/side_effects.py（T3c）调用；
# users 的 create_user / get_user_by_username / get_user_by_id / register_user /
# verify_user_password / get_or_create_passwordless_user / update_user_password /
# increment_user_token_version 仍被 knowledges 域与 personnel 调用（后续批次）。
# 这些名字不再出现在下面的冻结集合里：再加回 Database 会被 DW201 拦下。
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
    "add_message",
    "get_session",
    "update_session_workspace_name",
    "update_generated_file_paths",
    "update_last_assistant_message",
    "update_last_assistant_message_row",
    "create_user",
    "get_user_by_username",
    "get_user_by_id",
    "increment_user_token_version",
    "register_user",
    "verify_user_password",
    "get_or_create_passwordless_user",
    "update_user_password",
    "issue_mcp_api_key",
    "revoke_mcp_api_key",
    "get_mcp_api_keys",
    "add_generated_file",
    "get_generated_files",
    "delete_generated_file",
    "create_scheduled_task",
    "get_scheduled_task",
    "list_scheduled_tasks",
    "list_all_enabled_scheduled_tasks",
    "delete_scheduled_task",
    "update_scheduled_task_status",
    "update_scheduled_task_run_times",
    "update_scheduled_task_workspace",
    "add_scheduled_task_run",
    "update_scheduled_task_run",
    "list_scheduled_task_runs",
    "ping",
    "store_get",
    "store_put",
    "store_delete",
    "store_search",
    "store_list_namespaces",
    "get_session_runtime",
    "try_acquire_session_stream",
    "renew_session_stream",
    "upsert_session_runtime",
    "append_stream_event",
    "list_stream_events",
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
# T3b（2026-11-01 文件/用户域）实测：78 / 43 / 15 / 12。
MAX_DATABASE_METHODS = 78
MAX_DATABASE_PURE_DELEGATIONS = 43
MAX_GET_DATABASE_CALLS_OUTSIDE_DB = 15
MAX_DATABASE_ESCAPE_HATCH_REFS = 12
