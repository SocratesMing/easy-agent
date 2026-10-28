"""数据库结构定义与幂等迁移（ARCH-03 Phase 2 收尾）。

从 Database 抽出的 DDL/迁移逻辑：建表、补列、建索引、schema 版本快速路径、
消息行自愈。连接/事务仍由传入的 db 对象提供；Database 保留同名薄委托。
"""

from __future__ import annotations

import logging
import time

from easy_agent.db.legacy_messages import repair_missing_message_rows
from easy_agent.db.migrations import (
    BASELINE_VERSION,
    CLEAR_LEGACY_BLOBS_VERSION,
    META_TABLE,
    applied_versions,
    apply_pending_migrations,
    ensure_meta_table,
    ensure_migration_ledger,
    record_baseline,
)

from easy_agent.db.migration_lock import schema_migration_lock

logger = logging.getLogger("easy_agent.db.schema")


# 库表结构版本号。**任何建表 / 加列 / 改列 / 建索引 / 新建表的变更都必须把它 +1**，
# 否则已经升级过的库会一直命中「结构已是最新」的快速启动路径，变更不会生效。
# 见 init_tables()：版本一致时跳过整轮建表 + 补列 + 建索引探测（MySQL 上这一步
# 过去要 40+ 次网络往返，多副本部署时每个 pod 启动都要跑一遍，是启动慢的主因）。
# 10：knowledge_source_sync_items（b65aff4）；11：表精简（b7bfe69）；12：再退役 3 张空表；
# 13：再退役 8 张代码不再读写的表（旧 market key、行情数据、旧版知识库模块）。
# 撞号的库会走快速路径、跳过新的变更，改之前先看远端有没有用过这个号。
SCHEMA_VERSION = 13
# utf8mb4 复合主键必须 ≤ 3072 字节：(512 + 191) * 4 = 2812。
LANGGRAPH_STORE_NS_LEN = 512
LANGGRAPH_STORE_KEY_LEN = 191
# 结构版本号与迁移账本共用这张键值表（db/migrations.py）。
SCHEMA_VERSION_TABLE = META_TABLE
SCHEMA_VERSION_KEY = "schema_version"

# 快速路径的兜底校验：这些表必须全部存在，缺任何一个都回退到完整初始化。
# 新增表时除了加进这里，同样要 bump SCHEMA_VERSION。
# knowledge_* 由 knowledges/ 模块（v2）在完整初始化里创建，一并列出以便缺失时自愈。
EXPECTED_TABLES = (
    "sessions",
    "session_messages",
    "session_files",
    "generated_files",
    "users",
    "mcp_api_keys",
    "scheduled_tasks",
    "scheduled_task_runs",
    "distributed_locks",
    "session_runtime",
    "langgraph_store",
    "knowledge_bases",
    "knowledge_folders",
    "knowledge_documents",
    "knowledge_document_objects",
    "knowledge_operations",
    "knowledge_audit_events",
    "knowledge_source_sync_items",
    "expert_agents",
    "rate_limit_counters",
    "stream_events",
    "agent_runs",
)


def _create_index(db, cursor, index_name: str, table_name: str, columns: str):
    if db.db_type == "sqlite":
        cursor.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({columns})"
        )
    else:
        try:
            cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({columns})")
        except Exception as e:
            if "Duplicate key name" in str(e) or "already exists" in str(e):
                pass
            else:
                raise
def _create_sessions_table(db, cursor, messages_type):
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id VARCHAR(255) PRIMARY KEY,
            title TEXT NOT NULL,
            messages {messages_type} NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            username VARCHAR(255) DEFAULT ''
        )
    """)
    _ensure_column(db,
        cursor, "sessions", "username", "VARCHAR(255) DEFAULT ''"
    )
    _ensure_column(db,
        cursor, "sessions", "workspace_name", "VARCHAR(255) DEFAULT ''"
    )
    _ensure_column(db, cursor, "sessions", "pinned", "INTEGER DEFAULT 0")

    if db.db_type == "mysql":
        # 只在实际类型不满足时改列：MODIFY 会重建整表，sessions 表较大时
        # 单条就可能耗时数秒，每次启动都执行是完全没有必要的开销。
        messages_col_type = _column_type(db, cursor, "sessions", "messages")
        if "longtext" not in messages_col_type:
            try:
                cursor.execute(
                    "ALTER TABLE sessions MODIFY COLUMN messages LONGTEXT NOT NULL"
                )
            except Exception:
                pass

    _ensure_column(db, cursor, "sessions", "todos", "TEXT DEFAULT NULL")
    # 列表不再对 session_messages 做逐行 COUNT；插入/删除消息时维护这一列。
    _ensure_column(db, cursor, "sessions", "message_count", "INTEGER NOT NULL DEFAULT 0")
    # 列表按 (username?) + pinned DESC, updated_at DESC 取前 N 条；按用户名计数也走第一个
    # 的前缀，所以不再单建 username / updated_at 索引（见 _RETIRED_INDEXES）。
    _create_index(
        db, cursor, "idx_sessions_user_list", "sessions", "username, pinned, updated_at"
    )
    _create_index(
        db, cursor, "idx_sessions_pin_updated", "sessions", "pinned, updated_at"
    )

def _create_session_messages_table(db, cursor, auto_inc):
    text_type = "TEXT" if db.db_type == "sqlite" else "MEDIUMTEXT"

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS session_messages (
            id INTEGER PRIMARY KEY {auto_inc},
            session_id VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL,
            content {text_type},
            extra_data {text_type},
            created_at VARCHAR(50) NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )
    """)
    # (session_id) 索引按 id 有序：取全部消息直接用它，按角色取最后一条倒着扫一两行即可，
    # 不再单建 (session_id, role)。
    _create_index(db,
        cursor, "idx_session_messages_session", "session_messages", "session_id"
    )

    if db.db_type == "mysql":
        # 同 sessions.messages：列类型已满足时不再 MODIFY（避免整表重建）。
        if "mediumtext" not in _column_type(db, 
            cursor, "session_messages", "content"
        ):
            try:
                cursor.execute(
                    "ALTER TABLE session_messages MODIFY COLUMN content MEDIUMTEXT"
                )
            except Exception:
                pass
        if "mediumtext" not in _column_type(db, 
            cursor, "session_messages", "extra_data"
        ):
            try:
                cursor.execute(
                    "ALTER TABLE session_messages MODIFY COLUMN extra_data MEDIUMTEXT"
                )
            except Exception:
                pass

def _create_files_tables(db, cursor, auto_inc):
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS session_files (
            id INTEGER PRIMARY KEY {auto_inc},
            session_id VARCHAR(255) NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type VARCHAR(50) NOT NULL,
            size INTEGER NOT NULL,
            uploaded_at VARCHAR(50) NOT NULL,
            username VARCHAR(255) DEFAULT '',
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )
    """)
    _ensure_column(db, cursor, "session_files", "username", "TEXT DEFAULT ''")

    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS generated_files (
            id INTEGER PRIMARY KEY {auto_inc},
            session_id VARCHAR(255) NOT NULL,
            message_id VARCHAR(255) NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_type VARCHAR(50) NOT NULL,
            size INTEGER NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
        )
    """)
    _create_index(db,
        cursor, "idx_generated_files_session", "generated_files", "session_id"
    )
    _create_index(db,
        cursor, "idx_session_files_session", "session_files", "session_id"
    )
    _create_index(db, 
        cursor, "idx_session_files_username", "session_files", "username"
    )

def _create_users_table(db, cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id VARCHAR(255) PRIMARY KEY,
            username VARCHAR(255) NOT NULL UNIQUE,
            password_hash VARCHAR(255) DEFAULT '',
            organization_id VARCHAR(255) DEFAULT '',
            email VARCHAR(255) DEFAULT '',
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL
        )
    """)
    for col in ["password_hash", "organization_id", "email"]:
        _ensure_column(db, cursor, "users", col, "VARCHAR(255) DEFAULT ''")
    _ensure_column(db, cursor, "users", "bound_ip", "VARCHAR(45) DEFAULT ''")
    # 单点登录：token 版本号，每次登录递增，旧 token 的 v 不匹配即被踢下线
    _ensure_column(db, cursor, "users", "token_version", "INTEGER DEFAULT 0")
    # 工号：注册必填、全局唯一，登录时可替代用户名。默认 NULL 而非空串——
    # 唯一索引允许多个 NULL，空串却只能有一个（免密自动注册的用户都没有工号）。
    _ensure_column(db, cursor, "users", "employee_id", "VARCHAR(255) DEFAULT NULL")
    # Older personnel imports stored an unset employee ID as an empty string.
    db._execute(cursor, "UPDATE users SET employee_id=NULL WHERE employee_id='' ")
    _ensure_unique_index(db, 
        cursor, "users", "idx_users_employee_id", "employee_id"
    )
    # 人员字段（knowledge 鉴权读取 account_status / organization_id 的前置迁移）。
    # 幂等补列并带默认值；department_id 从既有 organization_id 回填，兼容旧库。
    for col, col_def in (
        ("display_name", "VARCHAR(127) DEFAULT ''"),
        ("department_id", "VARCHAR(255) DEFAULT ''"),
        ("department_name", "VARCHAR(255) DEFAULT ''"),
        ("division_name", "VARCHAR(255) DEFAULT ''"),
        ("team_name", "VARCHAR(255) DEFAULT ''"),
        ("position", "VARCHAR(255) DEFAULT ''"),
        ("mobile", "VARCHAR(64) DEFAULT ''"),
        ("account_status", "VARCHAR(20) DEFAULT 'active'"),
        ("personnel_source", "VARCHAR(255) DEFAULT ''"),
    ):
        _ensure_column(db, cursor, "users", col, col_def)
    try:
        cursor.execute(
            "UPDATE users SET department_id=organization_id "
            "WHERE (department_id IS NULL OR department_id='')"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"回填 users.department_id 失败（忽略）: {e}")
    # 空闲超时：最近一次接口调用时间（Unix 时间戳）。持久化到共享数据库，
    # 保证 uvicorn 多 worker（--workers > 1）下各进程读到一致的登录活跃状态。
    _ensure_column(db, cursor, "users", "last_activity_at", "REAL")
    from easy_agent.db.user_fields_registry import ensure_user_fields

    ensure_user_fields(db, cursor)

def _create_misc_tables(db, cursor, auto_inc):
    # MCP API Key：主应用签发，mcp-server 子项目只读校验。
    # UNIQUE(username, business) 保证每用户每业务仅一把有效 key，重签即覆盖。
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS mcp_api_keys (
            id INTEGER PRIMARY KEY {auto_inc},
            username VARCHAR(64) NOT NULL,
            business VARCHAR(64) NOT NULL,
            key_hash CHAR(64) NOT NULL UNIQUE,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0,
            UNIQUE (username, business)
        )
    """)

def _create_rate_limit_table(db, cursor):
    """B3：多副本共享限流计数（固定窗口）。

    主键 (bucket, window_start) 让 UPDATE/INSERT 在 SQLite/MySQL 上都是原子条件写；
    只保留当前窗口及上一窗口，避免公网大基数 IP 无界增长。
    """
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS rate_limit_counters (
            bucket VARCHAR(512) NOT NULL,
            window_start INTEGER NOT NULL,
            count INTEGER NOT NULL DEFAULT 0,
            updated_at VARCHAR(50) NOT NULL,
            PRIMARY KEY (bucket, window_start)
        )
        """
    )


def _create_scheduled_task_tables(db, cursor):
    # 定时任务表
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            task_id VARCHAR(255) PRIMARY KEY,
            username VARCHAR(255) NOT NULL,
            session_id VARCHAR(255) DEFAULT '',
            name VARCHAR(255) NOT NULL,
            description TEXT,
            schedule_cron VARCHAR(255) NOT NULL,
            task_prompt TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            last_run_at VARCHAR(50) DEFAULT '',
            next_run_at VARCHAR(50) DEFAULT ''
        )
    """)
    _ensure_column(db, cursor, "scheduled_tasks", "workspace_name", "VARCHAR(255) DEFAULT ''")
    _create_index(db, cursor, "idx_scheduled_tasks_username", "scheduled_tasks", "username")
    _create_index(db, cursor, "idx_scheduled_tasks_enabled", "scheduled_tasks", "enabled")

    # 定时任务执行记录表
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS scheduled_task_runs (
            run_id VARCHAR(255) PRIMARY KEY,
            task_id VARCHAR(255) NOT NULL,
            session_id VARCHAR(255) DEFAULT '',
            status VARCHAR(20) NOT NULL,
            started_at VARCHAR(50) NOT NULL,
            finished_at VARCHAR(50) DEFAULT '',
            result_summary TEXT,
            error_message TEXT
        )
    """)
    _create_index(db, cursor, "idx_scheduled_task_runs_task", "scheduled_task_runs", "task_id")


def _create_session_runtime_table(db, cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS session_runtime (
            session_id VARCHAR(255) PRIMARY KEY,
            connectors TEXT,
            edit_target VARCHAR(255) DEFAULT '',
            stream_owner VARCHAR(255) DEFAULT '',
            stream_until VARCHAR(50) DEFAULT '',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            updated_at VARCHAR(50) DEFAULT ''
        )
    """)

def _create_stream_events_table(db, cursor):
    """跨 Pod SSE 事件回放表（多副本 Phase 2A）。

    ``event_id`` 用自增主键提供库级全局顺序：多副本并发 append 时，回放端按
    ``event_id`` 升序分页（``after_id``）即可还原事件顺序。``payload`` 保存完整
    SSE frame（``data: {...}\n\n``），回放逐字节透传——不解析、不改写事件字段，
    因此 15 个事件的 wire 契约保持不变。
    """
    payload_type = "TEXT" if db.db_type == "sqlite" else "LONGTEXT"
    if db.db_type == "sqlite":
        event_id_def = "INTEGER PRIMARY KEY AUTOINCREMENT"
    else:
        event_id_def = "BIGINT PRIMARY KEY AUTO_INCREMENT"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS stream_events (
            event_id {event_id_def},
            session_id VARCHAR(255) NOT NULL,
            payload {payload_type} NOT NULL,
            created_at VARCHAR(50) NOT NULL
        )
    """)
    _create_index(
        db, cursor, "idx_stream_events_session", "stream_events", "session_id, event_id"
    )
    # 按时间清理走 created_at，(session_id, event_id) 覆盖不了这个条件。
    _create_index(
        db, cursor, "idx_stream_events_created", "stream_events", "created_at"
    )

def _create_agent_runs_table(db, cursor):
    """一次聊天/恢复 run 的持久化租约表（多副本 Phase 2B）。

    ``run_id`` 由 ``session_id + message_id`` 生成，主键保证幂等；
    ``lease_owner`` / ``lease_until`` 记录「哪个 Pod 正在跑、租约何时到期」。
    Pod 崩溃/驱逐后租约过期，启动时由 ``recover_stale_runs`` 把
    ``status='running'`` 的陈旧行标记为 ``interrupted``（不做自动重新执行）。
    时间统一 ``VARCHAR(50)`` 存 ISO 字符串，与 sessions/stream_events 约定一致。
    """
    request_type = "TEXT" if db.db_type == "sqlite" else "LONGTEXT"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id VARCHAR(255) PRIMARY KEY,
            session_id VARCHAR(255) NOT NULL,
            username VARCHAR(255) NOT NULL DEFAULT '',
            message_id VARCHAR(255) DEFAULT '',
            status VARCHAR(32) NOT NULL,
            request_json {request_type},
            lease_owner VARCHAR(255) DEFAULT '',
            lease_until VARCHAR(50) DEFAULT '',
            error TEXT,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL
        )
    """)
    _create_index(
        db, cursor, "idx_agent_runs_session", "agent_runs", "session_id, created_at"
    )
    _create_index(
        db, cursor, "idx_agent_runs_status", "agent_runs", "status, lease_until"
    )


def _create_langgraph_store_table(db, cursor):
    # InnoDB 索引上限 3072 字节。utf8mb4 下 VARCHAR(1024)+VARCHAR(255)
    # 的复合主键是 5116 字节，MySQL 报 1071。
    text_type = "TEXT" if db.db_type == "sqlite" else "LONGTEXT"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS langgraph_store (
            namespace VARCHAR({LANGGRAPH_STORE_NS_LEN}) NOT NULL,
            item_key VARCHAR({LANGGRAPH_STORE_KEY_LEN}) NOT NULL,
            value {text_type} NOT NULL,
            created_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL,
            PRIMARY KEY (namespace, item_key)
        )
    """)

# 曾经建过、当前路径不再读写的表。升级时删掉，避免继续占空间。
# fmqt_bloom*：9091087c 删掉彭博业务后留在库里，代码早已不建不读。
# uploaded_file / user_ip / tool_call_record：更早版本建的表，现在的代码里没有任何引用，现网是空表。
# market_mcp_api_keys：旧的 market 业务 key 表，key 统一放在 mcp_api_keys 之后不再使用。
# forex_rates / precious_metals：tests/helpers/download_market_data.py 灌的行情数据，后端不读不写。
# session_llm 不在这里：本应用从没建过这张表，归属确认之前不删。
_RETIRED_TABLES = (
    "fmqt_lock",
    "fmqt_bloom",
    "fmqt_bloom_analysis",
    "tool_call_records",
    "thinking_records",
    "agent_checkpoints",
    "uploaded_file",
    "user_ip",
    "tool_call_record",
    "market_mcp_api_keys",
    "forex_rates",
    "precious_metals",
)

# 不再创建的索引 (表, 索引)：都是别的索引的前缀，或者没有查询用到。升级时删掉，省掉写入开销。
_RETIRED_INDEXES = (
    ("sessions", "idx_updated_at"),
    ("sessions", "idx_sessions_username"),
    ("session_messages", "idx_session_messages_session_role"),
    ("generated_files", "idx_generated_files_message"),
    ("mcp_api_keys", "idx_mcp_api_keys_user"),
    ("expert_agents", "idx_expert_agents_status"),
)


def _drop_retired_tables(db, cursor) -> None:
    for name in _RETIRED_TABLES:
        try:
            cursor.execute(f"DROP TABLE IF EXISTS {name}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除废弃表 %s 失败（可忽略）: %s", name, exc)


def _drop_retired_indexes(db, cursor) -> None:
    """MySQL 没有 DROP INDEX IF EXISTS，逐个尝试；索引不存在（新库）即目标状态。"""
    for table, index in _RETIRED_INDEXES:
        try:
            if db.db_type == "mysql":
                cursor.execute(f"DROP INDEX `{index}` ON `{table}`")
            else:
                cursor.execute(f"DROP INDEX IF EXISTS `{index}`")
        except Exception:  # noqa: BLE001
            pass


def _backfill_message_counts(db, cursor) -> None:
    """用行表条数回填 sessions.message_count（升级当次；之后由写入路径维护）。"""
    db._execute(
        cursor,
        """
        UPDATE sessions SET message_count = (
            SELECT COUNT(*) FROM session_messages m
            WHERE m.session_id = sessions.session_id
        )
        """,
    )


def _create_distributed_lock_table(db, cursor):
    # 分布式锁表：多实例（多 pod）部署下防止同一任务被重复处理，
    # 见 easy_agent/utils/distributed_lock.py。lock_key 为主键，抢锁即
    # 「插入行 / 已过期则改写 owner」，插入失败即表示锁被其它实例占用。
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS distributed_locks (
            lock_key VARCHAR(255) NOT NULL PRIMARY KEY,
            owner VARCHAR(255) NOT NULL,
            acquired_at VARCHAR(50) NOT NULL,
            expires_at VARCHAR(50) NOT NULL,
            updated_at VARCHAR(50) NOT NULL
        )
    """)
    _create_index(db, 
        cursor, "idx_distributed_locks_expires", "distributed_locks", "expires_at"
    )


def init_tables(db):
    """迁移入口：跨进程迁移锁内执行完整初始化/升级。"""
    with schema_migration_lock(db):
        return _init_tables_locked(db)


def verify_schema_current(db) -> None:
    """启动只校验模式（EASY_AUTO_MIGRATE=0）：结构不匹配直接失败。

    K8s/多副本部署可先跑 `python scripts/db/migrate_db.py`（Job），Pod 启动只读
    校验，避免每个 Pod 并发执行 DDL。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        current = _ensure_schema_meta_table(db, cursor)
        conn.commit()
        present = _schema_tables_present(db, cursor)
    if current != SCHEMA_VERSION or not present:
        raise RuntimeError(
            "数据库结构不是最新（"
            f"schema_version={current}，期望 {SCHEMA_VERSION}，"
            f"关键表齐全={present}）。请先运行：python scripts/db/migrate_db.py"
        )


def _init_tables_locked(db):
    """幂等地初始化库表结构。

    启动性能：MySQL 场景下完整建表流程要 40+ 次网络往返（建表建索引 +
    每张表 SHOW COLUMNS + ALTER MODIFY 改列 + 全量消息修复扫描），多副本
    部署时每个 pod 启动都要跑一遍，是「连接 MySQL 很久」的主因。这里加一层
    schema 版本快速路径：库里记录的结构版本与代码一致、且期望的表都在时，
    只发 3 条探测 SQL 就返回，不再重复建表/补列/扫描。

    **改动任何表结构后必须把 SCHEMA_VERSION +1**，否则旧库不会走完整迁移。
    """
    started = time.monotonic()
    with db.get_connection() as conn:
        cursor = conn.cursor()

        current_version = _ensure_schema_meta_table(db, cursor)
        conn.commit()

        if current_version == SCHEMA_VERSION and _schema_tables_present(db, cursor):
            logger.info(
                f"数据库结构已是最新（schema_version={SCHEMA_VERSION}），"
                f"跳过建表检查 | 耗时 {time.monotonic() - started:.2f}s"
            )
            # 结构无需迁移时仍跑一次轻量数据自愈：只发 1 条聚合 SQL 比对
            # 条数，只有真的不一致的会话才会被重建。
            _repair_missing_message_rows(db, cursor)
            ensure_migration_ledger(db, cursor)
            record_baseline(db, cursor, BASELINE_VERSION)
            apply_pending_migrations(db, cursor)
            conn.commit()
            return

        _init_tables_full(db, cursor)
        conn.commit()

        # 修复 session_messages 表中缺失的消息行（仅在结构变更/首次启动时执行）
        _repair_missing_message_rows(db, cursor)
        _backfill_message_counts(db, cursor)
        ensure_migration_ledger(db, cursor)
        record_baseline(db, cursor, BASELINE_VERSION)
        apply_pending_migrations(db, cursor)
        _store_schema_version(db, cursor)
        conn.commit()

    logger.info(f"数据库结构初始化/升级完成 | 耗时 {time.monotonic() - started:.2f}s")

def _init_tables_full(db, cursor):
    """完整建表 + 补列 + 建索引流程（仅在结构变更或首次启动时执行）。"""
    db._column_cache.clear()
    db._column_type_cache.clear()

    auto_inc = "AUTOINCREMENT" if db.db_type == "sqlite" else "AUTO_INCREMENT"

    # MySQL 用 LONGTEXT(4GB)：会话消息不截断落库后体积可达数十 MB，
    # MEDIUMTEXT(16MB) 会触发 1406 Data too long 导致保存失败。
    # SQLite 的 TEXT 无实际长度限制，保持不变。
    messages_type = "TEXT" if db.db_type == "sqlite" else "LONGTEXT"

    _drop_retired_tables(db, cursor)
    _drop_retired_indexes(db, cursor)
    _create_sessions_table(db, cursor, messages_type)
    _create_session_messages_table(db, cursor, auto_inc)
    _create_files_tables(db, cursor, auto_inc)
    _create_users_table(db, cursor)
    _create_misc_tables(db, cursor, auto_inc)
    _create_scheduled_task_tables(db, cursor)
    _create_rate_limit_table(db, cursor)
    _create_distributed_lock_table(db, cursor)
    _create_session_runtime_table(db, cursor)
    _create_stream_events_table(db, cursor)
    _create_agent_runs_table(db, cursor)
    _create_langgraph_store_table(db, cursor)
    _create_expert_tables(db, cursor)

def _create_expert_tables(db, cursor):
    """专家 Agent 表 expert_agents（见 docs/expert-agent-solution.md §3.1）。

    建表口径：
    - ``export_run_file_path`` 按需求「（删除）」不建；
    - ``export_skills_names``：专家技能标签（中文名数组，JSON 字符串），由大模型
      根据用户描述生成 2~3 个；前端卡片最多展示 3 个；
    - ``export_description``：专家主要功能描述（大模型生成，≤300 字），
      用于卡片与详情页展示；
    - ``tag_id``：分类标签，由大模型在配置的 ``expert_tags`` 候选中选一个，
      前端「专家」Tab 的筛选 chips 按它过滤（``tag_name`` 取自配置文件）；
    - 时间统一 ``VARCHAR(50)`` 存 ISO 字符串，与 sessions/scheduled_tasks 现有
      约定一致，规避 SQLite/MySQL 双栈的 DATETIME 类型分歧；
    - 列表型字段（skills_names / features_prompt）用 TEXT 存 JSON 字符串；
    - 不加外键约束，只建索引，与本项目其余表一致。
    """
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS expert_agents (
            export_agent_id VARCHAR(255) PRIMARY KEY,
            session_id VARCHAR(255) DEFAULT '',
            user_id VARCHAR(255) NOT NULL,
            tag_id VARCHAR(255) DEFAULT '',
            export_name_en VARCHAR(255) NOT NULL,
            export_name_cn VARCHAR(255) NOT NULL,
            export_prompt TEXT,
            export_description TEXT,
            export_skills_names TEXT,
            export_features_prompt TEXT,
            export_file_path VARCHAR(1024) DEFAULT '',
            agent_status VARCHAR(32) NOT NULL DEFAULT 'active',
            export_version INTEGER NOT NULL DEFAULT 1,
            create_timestamp VARCHAR(50) NOT NULL,
            update_timestamp VARCHAR(50) NOT NULL
        )
    """)
    # 查询都带 user_id；agent_status 只有几种取值且多用 <>，单建索引用不上。
    _create_index(db,
        cursor, "idx_expert_agents_user", "expert_agents", "user_id"
    )

    # 领域 schema 由各模块在 import 时注册（db.schema_registry），这里统一执行；
    # 依赖方向 db -> schema_registry <- knowledges，避免 db 反向 import 领域模块。
    # 注意：knowledge 表结构变更仍需 bump SCHEMA_VERSION（见
    # tests/db/test_knowledge_schema_init.py）。
    from easy_agent.db.schema_registry import run_schema_initializers

    run_schema_initializers(db, cursor)

def _ensure_schema_meta_table(db, cursor) -> int:
    """建 schema_meta 表（若缺）并返回库中记录的结构版本号（无记录返回 0）。"""
    ensure_meta_table(db, cursor)
    db._execute(
        cursor,
        f"SELECT meta_value FROM {SCHEMA_VERSION_TABLE} WHERE meta_key=?",
        (SCHEMA_VERSION_KEY,),
    )
    row = cursor.fetchone()
    if not row:
        return 0
    raw = row["meta_value"] if isinstance(row, dict) else row[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0

def _store_schema_version(db, cursor) -> None:
    value = str(SCHEMA_VERSION)
    if db.db_type == "sqlite":
        db._execute(
            cursor,
            f"INSERT OR REPLACE INTO {SCHEMA_VERSION_TABLE} "
            "(meta_key, meta_value) VALUES (?, ?)",
            (SCHEMA_VERSION_KEY, value),
        )
    else:
        db._execute(
            cursor,
            f"INSERT INTO {SCHEMA_VERSION_TABLE} (meta_key, meta_value) VALUES (?, ?) "
            "ON DUPLICATE KEY UPDATE meta_value=VALUES(meta_value)",
            (SCHEMA_VERSION_KEY, value),
        )

def _schema_tables_present(db, cursor) -> bool:
    """快速校验关键表是否齐全；有任何缺失都回退到完整初始化。"""
    try:
        if db.db_type == "sqlite":
            db._execute(
                cursor, "SELECT name FROM sqlite_master WHERE type='table'"
            )
            rows = cursor.fetchall()
            names = {r["name"] if isinstance(r, dict) else r[0] for r in rows}
        else:
            db._execute(cursor, "SHOW TABLES")
            rows = cursor.fetchall()
            names = set()
            for r in rows:
                if isinstance(r, dict):
                    names.update(r.values())
                else:
                    names.add(r[0])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取表清单失败，回退到完整初始化: {e}")
        return False
    return all(t in names for t in EXPECTED_TABLES)

def _repair_missing_message_rows(db, cursor):
    """启动数据自愈：只补不删，见 :mod:`easy_agent.db.legacy_messages`。

    原实现在 JSON 条数与行数不等时删掉整个会话的行再按 JSON 重建。A2 之后旧会话的 JSON
    不再更新，继续对话的旧会话每次重启都会丢掉 A2 之后的消息。

    迁移 6 在第一次启动的末尾才执行：还没执行过，说明这是升级后的第一次启动，对不上的会话
    打 warning 留给人工核对；之后每次启动只记 debug。
    """
    try:
        first_start = CLEAR_LEGACY_BLOBS_VERSION not in applied_versions(db, cursor)
        repair_missing_message_rows(db, cursor, warn=first_start)
    except Exception as e:
        logger.warning(f"修复 session_messages 缺失行时出错: {e}")

def _find_mismatched_sessions(db, cursor) -> set[str]:
    """返回 messages JSON 条数与 session_messages 行数不一致的会话 id 集合。

    单条 SQL 完成比对（不走 Python 逐会话循环），MySQL 用 JSON_LENGTH，
    SQLite 用 json_array_length；两侧都要求 messages 是合法 JSON 数组，
    避免脏数据 / 非数组结构被误判或触发函数报错。
    """
    if db.db_type == "sqlite":
        sql = (
            "SELECT s.session_id FROM sessions s "
            "WHERE json_valid(s.messages) AND json_type(s.messages) = 'array' "
            "AND json_array_length(s.messages) <> ("
            "  SELECT COUNT(*) FROM session_messages m "
            "  WHERE m.session_id = s.session_id)"
        )
    else:
        sql = (
            "SELECT s.session_id FROM sessions s "
            "WHERE JSON_VALID(s.messages) AND JSON_TYPE(s.messages) = 'ARRAY' "
            "AND JSON_LENGTH(s.messages) <> ("
            "  SELECT COUNT(*) FROM session_messages m "
            "  WHERE m.session_id = s.session_id)"
        )
    db._execute(cursor, sql)
    rows = cursor.fetchall()
    return {r["session_id"] if isinstance(r, dict) else r[0] for r in rows}

def _ensure_column(db, cursor, table: str, column: str, col_def: str):
    columns = _table_columns(db, cursor, table)
    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        columns.add(column)

def _table_columns(db, cursor, table: str) -> set[str]:
    """返回表的所有列名（同一轮初始化内缓存，避免重复 SHOW COLUMNS / PRAGMA）。

    users 一张表历史上要探测 8 次列，缓存后每张表最多查一次；MySQL 下每次
    SHOW COLUMNS 都是一次网络往返，这是启动慢的次要来源之一。
    """
    cached = db._column_cache.get(table)
    if cached is not None:
        return cached
    if db.db_type == "sqlite":
        cursor.execute(f"PRAGMA table_info({table})")
        columns = {col[1] for col in cursor.fetchall()}
    else:
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        rows = cursor.fetchall()
        columns = set()
        types: dict[str, str] = {}
        for col in rows:
            if isinstance(col, dict):
                name = col.get("Field", "")
                types[name] = str(col.get("Type", ""))
            else:
                name = col[0]
                types[name] = str(col[1]) if len(col) > 1 else ""
            columns.add(name)
        db._column_type_cache[table] = types
    db._column_cache[table] = columns
    return columns

def _column_type(db, cursor, table: str, column: str) -> str:
    """返回列类型（小写，仅 MySQL 有意义）；缓存未命中时先填充表的列信息。"""
    if table not in db._column_type_cache:
        _table_columns(db, cursor, table)
    return db._column_type_cache.get(table, {}).get(column, "").lower()

def _ensure_unique_index(db, cursor, table: str, index_name: str, column: str
) -> None:
    """幂等地建唯一索引（两套方言没有统一的 IF NOT EXISTS 写法）。

    失败只告警、不中断启动：唯一性仍由应用层（register_user）先行校验，
    索引只负责在并发写入时兜底。
    """
    if db.db_type == "sqlite":
        stmt = (
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
            f"ON {table}({column})"
        )
    else:
        # MySQL 不支持 CREATE INDEX IF NOT EXISTS，重复创建会报 1061，忽略即可
        stmt = f"CREATE UNIQUE INDEX {index_name} ON {table}({column})"
    try:
        cursor.execute(stmt)
    except Exception as e:
        logger.debug(f"唯一索引 {index_name} 未创建（通常为已存在）: {e}")

def _create_unique_index(db, cursor, index_name: str, table_name: str, columns: str
) -> None:
    # knowledge/schema.py 沿用 sunya 的参数顺序（索引名在前）；复用本地实现。
    _ensure_unique_index(db, cursor, table_name, index_name, columns)
