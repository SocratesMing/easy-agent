"""Database management for Easy Agent

Supports SQLite3 (local) and MySQL (remote) databases
"""

import logging
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from typing import Optional

try:
    import pymysql
except ImportError:
    pymysql = None

try:
    from dbutils.pooled_db import PooledDB
except ImportError:
    PooledDB = None

from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel
from easy_agent.db.repositories import session_runtime as session_runtime_repo
from easy_agent.db.repositories import stream_events as stream_events_repo
from easy_agent.db.repositories import agent_runs as agent_runs_repo
from easy_agent.db.repositories import agent_state as agent_state_repo
from easy_agent.db.repositories import scheduled_tasks as scheduled_tasks_repo
from easy_agent.db.repositories import session_records as session_records_repo
from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import mcp_api_keys as mcp_api_keys_repo
from easy_agent.db.repositories import users as users_repo
from easy_agent.db import schema
from easy_agent.bootstrap.env import env_bool

DATABASE_PATH = "./data/easy_agent.db"
logger = logging.getLogger(__name__)

from easy_agent.db.schema import (  # noqa: F401 - 兼容重导出（tests/db 从这里取常量）
    EXPECTED_TABLES,
    LANGGRAPH_STORE_KEY_LEN,
    LANGGRAPH_STORE_NS_LEN,
    SCHEMA_VERSION,
    SCHEMA_VERSION_KEY,
)



def ensure_database_dir(db_path: str):
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)


class Database:
    def __init__(self, db_config: dict = None):
        self.db_type = "sqlite"
        self._connection: Optional[sqlite3.Connection] = None
        self._pool = None
        # SQLite 单连接被多线程共享（check_same_thread=False），必须串行化访问，
        # 否则并发 commit 会触发 SystemError / cannot commit - no transaction is active
        self._sqlite_lock = threading.RLock()
        # 每张表的列名缓存：一轮初始化里 _ensure_column 会对同一张表查很多次
        # （users 就有 8 次），缓存后每张表最多一次 SHOW COLUMNS / PRAGMA。
        self._column_cache: dict[str, set[str]] = {}
        # 每张表的「列名 -> 类型」缓存（仅 MySQL 用），用于判断 ALTER MODIFY 是否
        # 真的需要执行：MODIFY 会重建整表，sessions 表几十 MB 时单条就可能几秒。
        self._column_type_cache: dict[str, dict[str, str]] = {}

        if db_config:
            configured_type = db_config.get("type", "sqlite")

            if configured_type == "sqlite":
                sqlite_cfg = db_config.get("sqlite", {})
                self.db_path = sqlite_cfg.get("path", DATABASE_PATH)
                ensure_database_dir(self.db_path)
                logger.info(f"数据库类型: SQLite | 路径: {self.db_path}")

            elif configured_type == "mysql":
                self._mysql_config = db_config.get("mysql", {})
                try:
                    self._init_mysql_pool()
                    self.db_type = "mysql"
                    logger.info(
                        f"数据库类型: MySQL | "
                        f"主机: {self._mysql_config.get('host')}:{self._mysql_config.get('port')} | "
                        f"数据库: {self._mysql_config.get('database')} | 用户: {self._mysql_config.get('user')}"
                    )
                except Exception as e:
                    raise RuntimeError(
                        f"MySQL 连接失败，已禁止降级到 SQLite（多副本会各写各的库）: {e}"
                    ) from e
        else:
            self.db_path = DATABASE_PATH
            ensure_database_dir(self.db_path)
            logger.info(f"数据库类型: SQLite | 路径: {self.db_path}")

    def _init_mysql_pool(self):
        db_name = self._mysql_config.get("database", "easy_agent")
        host = self._mysql_config.get("host", "localhost")
        port = self._mysql_config.get("port", 3306)
        user = self._mysql_config.get("user", "root")
        password = self._mysql_config.get("password", "")
        charset = self._mysql_config.get("charset", "utf8mb4")
        connect_timeout = self._mysql_config.get("connect_timeout", 10)
        target = f"{user}@{host}:{port}/{db_name}"

        if not password:
            logger.warning(
                "MySQL 密码为空：请检查当前环境配置文件中的 database.mysql.password，"
                "并直接填写真实密码（改完必须重启后端）"
            )

        try:
            conn = pymysql.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                charset=charset,
                connect_timeout=connect_timeout,
            )
            cursor = conn.cursor()
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            cursor.close()
            conn.close()
            logger.info(f"MySQL数据库 '{db_name}' 已就绪")
        except Exception as e:
            err = str(e)
            if "using password: NO" in err:
                hint = "（未发送密码：请检查 database.mysql.password 是否为空）"
            elif "using password: YES" in err:
                hint = "（密码错误，或该用户未对来源 host 授权，如 root@'172.17.0.1'）"
            else:
                hint = "（库已存在或无 CREATE 权限时可忽略）"
            logger.warning(f"创建/连接 MySQL 数据库失败{hint}: {e} | 目标: {target}")

        pool_cfg = self._mysql_config.get("pool", {})
        self._pool = PooledDB(
            creator=pymysql,
            maxconnections=pool_cfg.get("pool_size", 5)
            + pool_cfg.get("max_overflow", 10),
            mincached=1,
            maxcached=pool_cfg.get("pool_size", 5),
            blocking=True,
            maxusage=pool_cfg.get("pool_recycle", 3600),
            host=host,
            port=port,
            user=user,
            password=password,
            database=db_name,
            charset=charset,
            connect_timeout=connect_timeout,
            read_timeout=self._mysql_config.get("read_timeout", 30),
            write_timeout=self._mysql_config.get("write_timeout", 30),
            cursorclass=pymysql.cursors.DictCursor,
        )

        try:
            conn = self._pool.connection()
            cursor = conn.cursor()
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()
            cursor.close()
            conn.close()
        except Exception as e:
            raise ConnectionError(f"MySQL 连接失败 | 目标: {target} | 原因: {e}") from e

        ver_str = version["VERSION()"] if version else "未知"
        logger.info(f"MySQL连接成功 | 版本: {ver_str}")

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30,
            )
            self._connection.row_factory = sqlite3.Row
            # 单机/开发默认 SQLite：开启 WAL 提升读写并发，busy_timeout 降低跨进程
            # 写入时 "database is locked" 概率；多副本生产仍要求 MySQL（见 deploy/k8s）。
            for _pragma in (
                "PRAGMA journal_mode=WAL",
                "PRAGMA synchronous=NORMAL",
                "PRAGMA busy_timeout=30000",
            ):
                try:
                    self._connection.execute(_pragma)
                except sqlite3.DatabaseError as _exc:  # 网络文件系统等不支持 WAL 时降级
                    logger.warning("SQLite pragma 执行失败 | %s | %s", _pragma, _exc)
        return self._connection

    def _get_mysql_connection(self):
        if self._pool is None:
            self._init_mysql_pool()
        return self._pool.connection()

    def _get_connection(self):
        if self.db_type == "mysql":
            return self._get_mysql_connection()
        return self._get_sqlite_connection()

    def close(self):
        if self.db_type == "mysql":
            if self._pool:
                self._pool.close()
                self._pool = None
        else:
            if self._connection:
                self._connection.close()
                self._connection = None

    @contextmanager
    def get_connection(self):
        if self.db_type == "mysql":
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            return
        # SQLite：单连接多线程共享，整段访问（含 commit/rollback）持锁串行化
        with self._sqlite_lock:
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _execute(self, cursor, sql: str, params: tuple = None):
        if self.db_type == "mysql":
            sql = re.sub(r"\?", "%s", sql)
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

    # ARCH-03 Phase 2: DDL/迁移已抽到 db/schema.py
    def _create_index(self, *args, **kwargs):
        return schema._create_index(self, *args, **kwargs)

    def _create_sessions_table(self, *args, **kwargs):
        return schema._create_sessions_table(self, *args, **kwargs)

    def _create_tool_call_records_table(self, *args, **kwargs):
        return schema._create_tool_call_records_table(self, *args, **kwargs)

    def _create_thinking_records_table(self, *args, **kwargs):
        return schema._create_thinking_records_table(self, *args, **kwargs)

    def _create_session_messages_table(self, *args, **kwargs):
        return schema._create_session_messages_table(self, *args, **kwargs)

    def _create_files_tables(self, *args, **kwargs):
        return schema._create_files_tables(self, *args, **kwargs)

    def _create_users_table(self, *args, **kwargs):
        return schema._create_users_table(self, *args, **kwargs)

    def _create_misc_tables(self, *args, **kwargs):
        return schema._create_misc_tables(self, *args, **kwargs)

    def _create_scheduled_task_tables(self, *args, **kwargs):
        return schema._create_scheduled_task_tables(self, *args, **kwargs)

    def _create_session_runtime_table(self, *args, **kwargs):
        return schema._create_session_runtime_table(self, *args, **kwargs)

    def _create_stream_events_table(self, *args, **kwargs):
        return schema._create_stream_events_table(self, *args, **kwargs)

    def _create_agent_runs_table(self, *args, **kwargs):
        return schema._create_agent_runs_table(self, *args, **kwargs)

    def _create_langgraph_store_table(self, *args, **kwargs):
        return schema._create_langgraph_store_table(self, *args, **kwargs)

    def _create_agent_checkpoint_table(self, *args, **kwargs):
        return schema._create_agent_checkpoint_table(self, *args, **kwargs)

    def _create_distributed_lock_table(self, *args, **kwargs):
        return schema._create_distributed_lock_table(self, *args, **kwargs)

    def init_tables(self, *args, **kwargs):
        return schema.init_tables(self, *args, **kwargs)

    def verify_schema_current(self) -> None:
        return schema.verify_schema_current(self)

    def _init_tables_full(self, *args, **kwargs):
        return schema._init_tables_full(self, *args, **kwargs)

    def _create_expert_tables(self, *args, **kwargs):
        return schema._create_expert_tables(self, *args, **kwargs)

    def _ensure_schema_meta_table(self, *args, **kwargs):
        return schema._ensure_schema_meta_table(self, *args, **kwargs)

    def _store_schema_version(self, *args, **kwargs):
        return schema._store_schema_version(self, *args, **kwargs)

    def _schema_tables_present(self, *args, **kwargs):
        return schema._schema_tables_present(self, *args, **kwargs)

    def _repair_missing_message_rows(self, *args, **kwargs):
        return schema._repair_missing_message_rows(self, *args, **kwargs)

    def _find_mismatched_sessions(self, *args, **kwargs):
        return schema._find_mismatched_sessions(self, *args, **kwargs)

    def _ensure_column(self, *args, **kwargs):
        return schema._ensure_column(self, *args, **kwargs)

    def _table_columns(self, *args, **kwargs):
        return schema._table_columns(self, *args, **kwargs)

    def _column_type(self, *args, **kwargs):
        return schema._column_type(self, *args, **kwargs)

    def _ensure_unique_index(self, *args, **kwargs):
        return schema._ensure_unique_index(self, *args, **kwargs)

    def _create_unique_index(self, *args, **kwargs):
        return schema._create_unique_index(self, *args, **kwargs)

    # ARCH-03 Phase 2: sessions/messages SQL 已抽到 db/repositories/sessions.py
    def create_session(self, *args, **kwargs):
        return sessions_repo.create_session(self, *args, **kwargs)

    def get_session_owner(self, *args, **kwargs):
        return sessions_repo.get_session_owner(self, *args, **kwargs)

    def _get_legacy_session_messages(self, *args, **kwargs):
        return sessions_repo._get_legacy_session_messages(self, *args, **kwargs)

    def update_session_meta(self, *args, **kwargs):
        return sessions_repo.update_session_meta(self, *args, **kwargs)

    def get_session(self, *args, **kwargs):
        return sessions_repo.get_session(self, *args, **kwargs)

    def list_sessions(self, *args, **kwargs):
        return sessions_repo.list_sessions(self, *args, **kwargs)

    def update_session(self, *args, **kwargs):
        return sessions_repo.update_session(self, *args, **kwargs)

    def delete_session(self, *args, **kwargs):
        return sessions_repo.delete_session(self, *args, **kwargs)

    def update_session_workspace_name(self, *args, **kwargs):
        return sessions_repo.update_session_workspace_name(self, *args, **kwargs)

    def update_session_todos(self, *args, **kwargs):
        return sessions_repo.update_session_todos(self, *args, **kwargs)

    def update_generated_file_paths(self, *args, **kwargs):
        return sessions_repo.update_generated_file_paths(self, *args, **kwargs)

    def add_message(self, *args, **kwargs):
        return sessions_repo.add_message(self, *args, **kwargs)

    def _sanitize_message_for_storage(self, *args, **kwargs):
        return sessions_repo._sanitize_message_for_storage(self, *args, **kwargs)

    def update_last_assistant_message(self, *args, **kwargs):
        return sessions_repo.update_last_assistant_message(self, *args, **kwargs)

    def _sanitize_blocks(self, *args, **kwargs):
        return sessions_repo._sanitize_blocks(self, *args, **kwargs)

    def _sanitize_tool_calls(self, *args, **kwargs):
        return sessions_repo._sanitize_tool_calls(self, *args, **kwargs)

    def _sanitize_extra_data(self, *args, **kwargs):
        return sessions_repo._sanitize_extra_data(self, *args, **kwargs)

    def add_message_row(self, *args, **kwargs):
        return sessions_repo.add_message_row(self, *args, **kwargs)

    def update_last_assistant_message_row(self, *args, **kwargs):
        return sessions_repo.update_last_assistant_message_row(self, *args, **kwargs)

    def get_messages_from_rows(self, *args, **kwargs):
        return sessions_repo.get_messages_from_rows(self, *args, **kwargs)

    def get_messages(self, *args, **kwargs):
        return sessions_repo.get_messages(self, *args, **kwargs)

    def count_sessions(self, *args, **kwargs):
        return sessions_repo.count_sessions(self, *args, **kwargs)

    def update_session_title(self, *args, **kwargs):
        return sessions_repo.update_session_title(self, *args, **kwargs)

    def toggle_session_pin(self, *args, **kwargs):
        return sessions_repo.toggle_session_pin(self, *args, **kwargs)

    @staticmethod
    def _try_repair_json(raw: str) -> dict | None:
        return sessions_repo.try_repair_json(raw)

    # ARCH-03 Phase 2: 工具/思考记录 SQL 已抽到 db/repositories/session_records.py
    def record_tool_call(self, session_id: str, message_id: str, tool_name: str,
                         tool_call_id: str, arguments: dict, result: str = None,
                         success: bool = True, duration: float = None, step: int = 0):
        return session_records_repo.record_tool_call(
            self, session_id, message_id, tool_name, tool_call_id, arguments,
            result, success, duration, step,
        )

    def get_tool_calls(self, session_id: str) -> list[dict]:
        return session_records_repo.get_tool_calls(self, session_id)

    def record_thinking(self, session_id: str, message_id: str, step: int,
                        content: str, duration: float = None):
        return session_records_repo.record_thinking(
            self, session_id, message_id, step, content, duration
        )

    def get_thinkings(self, session_id: str) -> list[dict]:
        return session_records_repo.get_thinkings(self, session_id)

    # ARCH-03 Phase 2: users SQL 已抽到 db/repositories/users.py
    def create_user(self, *args, **kwargs):
        return users_repo.create_user(self, *args, **kwargs)

    def get_user_by_username(self, *args, **kwargs):
        return users_repo.get_user_by_username(self, *args, **kwargs)

    def get_user_by_id(self, *args, **kwargs):
        return users_repo.get_user_by_id(self, *args, **kwargs)

    def get_user_by_employee_id(self, *args, **kwargs):
        return users_repo.get_user_by_employee_id(self, *args, **kwargs)

    def get_user_by_account(self, *args, **kwargs):
        return users_repo.get_user_by_account(self, *args, **kwargs)

    def update_user(self, *args, **kwargs):
        return users_repo.update_user(self, *args, **kwargs)

    def bind_user_ip(self, *args, **kwargs):
        return users_repo.bind_user_ip(self, *args, **kwargs)

    def get_user_bound_ip(self, *args, **kwargs):
        return users_repo.get_user_bound_ip(self, *args, **kwargs)

    def increment_user_token_version(self, *args, **kwargs):
        return users_repo.increment_user_token_version(self, *args, **kwargs)

    def get_user_token_version(self, *args, **kwargs):
        return users_repo.get_user_token_version(self, *args, **kwargs)

    def touch_user_activity(self, *args, **kwargs):
        return users_repo.touch_user_activity(self, *args, **kwargs)

    def get_user_activity_time(self, *args, **kwargs):
        return users_repo.get_user_activity_time(self, *args, **kwargs)

    def clear_user_activity(self, *args, **kwargs):
        return users_repo.clear_user_activity(self, *args, **kwargs)

    def register_user(self, *args, **kwargs):
        return users_repo.register_user(self, *args, **kwargs)

    def verify_user_password(self, *args, **kwargs):
        return users_repo.verify_user_password(self, *args, **kwargs)

    def get_or_create_passwordless_user(self, *args, **kwargs):
        return users_repo.get_or_create_passwordless_user(self, *args, **kwargs)

    def update_user_password(self, *args, **kwargs):
        return users_repo.update_user_password(self, *args, **kwargs)

    def delete_user(self, *args, **kwargs):
        return users_repo.delete_user(self, *args, **kwargs)

    def get_or_create_default_user(self, *args, **kwargs):
        return users_repo.get_or_create_default_user(self, *args, **kwargs)

    def list_users(self, *args, **kwargs):
        return users_repo.list_users(self, *args, **kwargs)

    def count_users(self, *args, **kwargs):
        return users_repo.count_users(self, *args, **kwargs)

    @staticmethod
    def _row_to_user(row):
        return users_repo.row_to_user(row)

    # ARCH-03 Phase 2: MCP API Key SQL 已抽到 db/repositories/mcp_api_keys.py
    def issue_mcp_api_key(self, *args, **kwargs):
        return mcp_api_keys_repo.issue_mcp_api_key(self, *args, **kwargs)

    def revoke_mcp_api_key(self, *args, **kwargs):
        return mcp_api_keys_repo.revoke_mcp_api_key(self, *args, **kwargs)

    def get_mcp_api_keys(self, *args, **kwargs):
        return mcp_api_keys_repo.get_mcp_api_keys(self, *args, **kwargs)

    # ARCH-03 Phase 2: 文件 SQL 已抽到 db/repositories/files.py
    def add_session_file(self, *args, **kwargs):
        return files_repo.add_session_file(self, *args, **kwargs)

    def get_session_files(self, session_id: str) -> list[dict]:
        return files_repo.get_session_files(self, session_id)

    def delete_file(self, file_id: int, username: str | None = None) -> bool:
        return files_repo.delete_file(self, file_id, username)

    def add_generated_file(self, *args, **kwargs):
        return files_repo.add_generated_file(self, *args, **kwargs)

    def get_generated_files(self, session_id: str) -> list[dict]:
        return files_repo.get_generated_files(self, session_id)

    def delete_generated_file(self, file_id: int) -> bool:
        return files_repo.delete_generated_file(self, file_id)

    def get_generated_filenames(self, username: str) -> set[str]:
        return files_repo.get_generated_filenames(self, username)

    # ARCH-03 Phase 2: 定时任务 SQL 已抽到 db/repositories/scheduled_tasks.py
    def create_scheduled_task(self, task: ScheduledTaskModel) -> ScheduledTaskModel:
        return scheduled_tasks_repo.create_scheduled_task(self, task)

    def get_scheduled_task(self, task_id: str) -> ScheduledTaskModel | None:
        return scheduled_tasks_repo.get_scheduled_task(self, task_id)

    def list_scheduled_tasks(self, username: str, enabled_only: bool = False) -> list[ScheduledTaskModel]:
        return scheduled_tasks_repo.list_scheduled_tasks(self, username, enabled_only=enabled_only)

    def list_all_enabled_scheduled_tasks(self) -> list[ScheduledTaskModel]:
        return scheduled_tasks_repo.list_all_enabled_scheduled_tasks(self)

    def delete_scheduled_task(self, task_id: str) -> bool:
        return scheduled_tasks_repo.delete_scheduled_task(self, task_id)

    def update_scheduled_task_status(self, task_id: str, enabled: bool):
        return scheduled_tasks_repo.update_scheduled_task_status(self, task_id, enabled)

    def update_scheduled_task_run_times(self, task_id: str, last_run_at: str, next_run_at: str):
        return scheduled_tasks_repo.update_scheduled_task_run_times(
            self, task_id, last_run_at, next_run_at
        )

    def update_scheduled_task_workspace(self, task_id: str, workspace_name: str):
        return scheduled_tasks_repo.update_scheduled_task_workspace(
            self, task_id, workspace_name
        )

    def add_scheduled_task_run(self, run: ScheduledTaskRunModel) -> ScheduledTaskRunModel:
        return scheduled_tasks_repo.add_scheduled_task_run(self, run)

    def update_scheduled_task_run(self, run_id: str, status: str, finished_at: str,
                                  result_summary: str = "", error_message: str = ""):
        return scheduled_tasks_repo.update_scheduled_task_run(
            self, run_id, status, finished_at, result_summary, error_message
        )

    def list_scheduled_task_runs(self, task_id: str, limit: int = 50) -> list[ScheduledTaskRunModel]:
        return scheduled_tasks_repo.list_scheduled_task_runs(self, task_id, limit)

    def ping(self) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "SELECT 1")
            cursor.fetchone()
        return True

    # ARCH-03 Phase 2: checkpoint/store SQL 已抽到 db/repositories/agent_state.py
    def load_agent_checkpoint(self, session_id: str) -> bytes | None:
        return agent_state_repo.load_agent_checkpoint(self, session_id)

    def save_agent_checkpoint(self, session_id: str, payload: bytes) -> None:
        return agent_state_repo.save_agent_checkpoint(self, session_id, payload)

    def delete_agent_checkpoint(self, session_id: str) -> None:
        return agent_state_repo.delete_agent_checkpoint(self, session_id)

    def store_get(self, namespace: str, item_key: str) -> dict | None:
        return agent_state_repo.store_get(self, namespace, item_key)

    def store_put(self, namespace: str, item_key: str, value: str) -> None:
        return agent_state_repo.store_put(self, namespace, item_key, value)

    def store_delete(self, namespace: str, item_key: str) -> None:
        return agent_state_repo.store_delete(self, namespace, item_key)

    def store_search(self, namespace_prefix: str, *, limit: int = 10, offset: int = 0) -> list[dict]:
        return agent_state_repo.store_search(
            self, namespace_prefix, limit=limit, offset=offset
        )

    def store_list_namespaces(self) -> list[str]:
        return agent_state_repo.store_list_namespaces(self)

    # ARCH-03 Phase 2: session_runtime SQL 已抽到 db/repositories/session_runtime.py
    def get_session_runtime(self, session_id: str) -> dict | None:
        return session_runtime_repo.get_session_runtime(self, session_id)

    def try_acquire_session_stream(
        self, session_id: str, owner: str, until_iso: str, now_iso: str
    ) -> tuple[bool, dict | None]:
        return session_runtime_repo.try_acquire_session_stream(
            self, session_id, owner, until_iso, now_iso
        )

    def renew_session_stream(
        self, session_id: str, owner: str, until_iso: str, now_iso: str
    ) -> bool:
        return session_runtime_repo.renew_session_stream(
            self, session_id, owner, until_iso, now_iso
        )

    def upsert_session_runtime(
        self,
        session_id: str,
        *,
        connectors=None,
        edit_target=None,
        stream_owner=None,
        stream_until=None,
        cancel_requested=None,
        clear_connectors: bool = False,
    ) -> None:
        session_runtime_repo.upsert_session_runtime(
            self,
            session_id,
            connectors=connectors,
            edit_target=edit_target,
            stream_owner=stream_owner,
            stream_until=stream_until,
            cancel_requested=cancel_requested,
            clear_connectors=clear_connectors,
        )

    # 多副本 Phase 2A: SSE 事件持久化已抽到 db/repositories/stream_events.py
    def append_stream_event(self, session_id: str, payload: str) -> int:
        return stream_events_repo.append_stream_event(self, session_id, payload)

    def list_stream_events(
        self, session_id: str, after_id: int = 0, limit: int = 500
    ) -> list[dict]:
        return stream_events_repo.list_stream_events(
            self, session_id, after_id=after_id, limit=limit
        )

    def delete_stream_events_before(self, cutoff_iso: str) -> int:
        return stream_events_repo.delete_stream_events_before(self, cutoff_iso)

    # 多副本 Phase 2B: run 租约已抽到 db/repositories/agent_runs.py
    def start_run(
        self,
        run_id: str,
        session_id: str,
        username: str,
        message_id: str,
        request_json: str,
        owner: str,
        lease_until: str,
    ) -> bool:
        return agent_runs_repo.start_run(
            self,
            run_id,
            session_id,
            username,
            message_id,
            request_json,
            owner,
            lease_until,
        )

    def renew_run(self, run_id: str, owner: str, lease_until: str) -> bool:
        return agent_runs_repo.renew_run(self, run_id, owner, lease_until)

    def finish_run(self, run_id: str, status: str, error: str = "") -> bool:
        return agent_runs_repo.finish_run(self, run_id, status, error)

    def get_run(self, run_id: str) -> dict | None:
        return agent_runs_repo.get_run(self, run_id)

    def list_stale_running(
        self, now_iso: str, limit: int = 500
    ) -> list[dict]:
        return agent_runs_repo.list_stale_running(self, now_iso, limit)

    def mark_interrupted(self, run_id: str, error: str) -> bool:
        return agent_runs_repo.mark_interrupted(self, run_id, error)


_db_instance = None


def init_database(db_config: dict = None) -> Database:
    global _db_instance
    db = Database(db_config)
    if env_bool("EASY_AUTO_MIGRATE", True):
        db.init_tables()
    else:
        # 多副本：迁移由一次性 Job/脚本完成，Pod 启动只校验，不并发 DDL。
        db.verify_schema_current()
    db.get_or_create_default_user()
    _db_instance = db
    logger.info("✅ 数据库初始化完成")
    return db


def get_database() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = init_database()
    return _db_instance
