"""Database management for Easy Agent

Supports SQLite3 (local) and MySQL (remote) databases
"""

import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Generator, Optional

import sqlite3
from pydantic import BaseModel

from .models import SessionModel, UserModel
from ..utils.auth import hash_password, verify_password

DATABASE_PATH = "./data/easy_agent.db"
logger = logging.getLogger(__name__)


def ensure_database_dir(db_path: str):
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir, exist_ok=True)


class Database:

    def __init__(self, db_config: dict = None):
        self.db_type = "sqlite"
        self._connection: Optional[sqlite3.Connection] = None
        self._pool = None

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
                    logger.warning(f"MySQL 连接失败，自动降级到 SQLite: {e}")
                    self.db_type = "sqlite"
                    self._pool = None
                    sqlite_cfg = db_config.get("sqlite", {})
                    self.db_path = sqlite_cfg.get("path", DATABASE_PATH)
                    ensure_database_dir(self.db_path)
                    logger.info(f"降级后数据库类型: SQLite | 路径: {self.db_path}")
        else:
            self.db_path = DATABASE_PATH
            ensure_database_dir(self.db_path)
            logger.info(f"数据库类型: SQLite | 路径: {self.db_path}")

    def _init_mysql_pool(self):
        from dbutils.pooled_db import PooledDB
        import pymysql

        db_name = self._mysql_config.get("database", "easy_agent")
        try:
            conn = pymysql.connect(
                host=self._mysql_config.get("host", "localhost"),
                port=self._mysql_config.get("port", 3306),
                user=self._mysql_config.get("user", "root"),
                password=self._mysql_config.get("password", ""),
                charset=self._mysql_config.get("charset", "utf8mb4"),
                connect_timeout=self._mysql_config.get("connect_timeout", 10),
            )
            cursor = conn.cursor()
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            cursor.close()
            conn.close()
            logger.info(f"MySQL数据库 '{db_name}' 已就绪")
        except Exception as e:
            logger.warning(f"创建数据库时出错（可能已存在）: {e}")

        pool_cfg = self._mysql_config.get("pool", {})
        self._pool = PooledDB(
            creator=pymysql,
            maxconnections=pool_cfg.get("pool_size", 5) + pool_cfg.get("max_overflow", 10),
            mincached=1,
            maxcached=pool_cfg.get("pool_size", 5),
            blocking=True,
            maxusage=pool_cfg.get("pool_recycle", 3600),
            host=self._mysql_config.get("host", "localhost"),
            port=self._mysql_config.get("port", 3306),
            user=self._mysql_config.get("user", "root"),
            password=self._mysql_config.get("password", ""),
            database=db_name,
            charset=self._mysql_config.get("charset", "utf8mb4"),
            connect_timeout=self._mysql_config.get("connect_timeout", 10),
            read_timeout=self._mysql_config.get("read_timeout", 30),
            write_timeout=self._mysql_config.get("write_timeout", 30),
            cursorclass=pymysql.cursors.DictCursor,
        )

        conn = self._pool.connection()
        cursor = conn.cursor()
        cursor.execute("SELECT VERSION()")
        version = cursor.fetchone()
        cursor.close()
        conn.close()
        ver_str = version['VERSION()'] if version else '未知'
        logger.info(f"MySQL连接成功 | 版本: {ver_str}")

    def _get_sqlite_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
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
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _execute(self, cursor, sql: str, params: tuple = None):
        if self.db_type == "mysql":
            sql = re.sub(r'\?', '%s', sql)
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)

    def _create_index(self, cursor, index_name: str, table_name: str, columns: str):
        if self.db_type == "sqlite":
            cursor.execute(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name}({columns})")
        else:
            try:
                cursor.execute(f"CREATE INDEX {index_name} ON {table_name}({columns})")
            except Exception as e:
                if "Duplicate key name" in str(e) or "already exists" in str(e):
                    pass
                else:
                    raise

    def init_tables(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()

            auto_inc = "AUTOINCREMENT" if self.db_type == "sqlite" else "AUTO_INCREMENT"

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id VARCHAR(255) PRIMARY KEY,
                    title TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    created_at VARCHAR(50) NOT NULL,
                    updated_at VARCHAR(50) NOT NULL,
                    username VARCHAR(255) DEFAULT ''
                )
            """)
            self._create_index(cursor, "idx_updated_at", "sessions", "updated_at")
            self._ensure_column(cursor, "sessions", "username", "VARCHAR(255) DEFAULT ''")
            self._create_index(cursor, "idx_sessions_username", "sessions", "username")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS tool_call_records (
                    id INTEGER PRIMARY KEY {auto_inc},
                    session_id VARCHAR(255) NOT NULL,
                    message_id VARCHAR(255) NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_call_id VARCHAR(255) NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT,
                    success INTEGER NOT NULL DEFAULT 1,
                    duration REAL,
                    step INTEGER NOT NULL DEFAULT 0,
                    created_at VARCHAR(50) NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
            """)
            self._create_index(cursor, "idx_tool_call_session", "tool_call_records", "session_id")
            self._create_index(cursor, "idx_tool_call_message", "tool_call_records", "session_id, message_id")
            # 迁移: 为已存在的 tool_call_records 添加新字段
            self._ensure_column(cursor, "tool_call_records", "duration", "REAL")
            self._ensure_column(cursor, "tool_call_records", "step", "INTEGER NOT NULL DEFAULT 0")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS thinking_records (
                    id INTEGER PRIMARY KEY {auto_inc},
                    session_id VARCHAR(255) NOT NULL,
                    message_id VARCHAR(255) NOT NULL,
                    step INTEGER NOT NULL DEFAULT 0,
                    content TEXT NOT NULL,
                    duration REAL,
                    created_at VARCHAR(50) NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                )
            """)
            self._create_index(cursor, "idx_thinking_session", "thinking_records", "session_id")
            self._create_index(cursor, "idx_thinking_message", "thinking_records", "session_id, message_id")

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
            self._ensure_column(cursor, "session_files", "username", "TEXT DEFAULT ''")

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
            self._create_index(cursor, "idx_generated_files_session", "generated_files", "session_id")
            self._create_index(cursor, "idx_generated_files_message", "generated_files", "session_id, message_id")
            self._create_index(cursor, "idx_session_files_session", "session_files", "session_id")
            self._create_index(cursor, "idx_session_files_username", "session_files", "username")

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
                self._ensure_column(cursor, "users", col, "VARCHAR(255) DEFAULT ''")
            # IP 绑定字段
            self._ensure_column(cursor, "users", "bound_ip", "VARCHAR(45) DEFAULT ''")

            conn.commit()

    def _ensure_column(self, cursor, table: str, column: str, col_def: str):
        if self.db_type == "sqlite":
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            if column not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
        else:
            cursor.execute(f"SHOW COLUMNS FROM {table}")
            columns = [col['Field'] if isinstance(col, dict) else col[0] for col in cursor.fetchall()]
            if column not in columns:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

    def create_session(self, session_data: SessionModel) -> SessionModel:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO sessions (session_id, title, messages, created_at, updated_at, username)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_data.session_id,
                    session_data.title,
                    json.dumps(session_data.messages, ensure_ascii=False),
                    session_data.created_at,
                    session_data.updated_at,
                    session_data.username,
                ),
            )
        return session_data

    def get_session(self, session_id: str) -> Optional[SessionModel]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                SELECT session_id, title, messages, created_at, updated_at, username
                FROM sessions WHERE session_id = ?
                """,
                (session_id,),
            )
            row = cursor.fetchone()

        if row is None:
            return None

        return SessionModel(
            session_id=row["session_id"],
            title=row["title"],
            messages=json.loads(row["messages"]) if row["messages"] else [],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            username=row.get("username", "") if isinstance(row, dict) else (row[-1] if len(row) > 5 else ""),
        )

    def list_sessions(
        self, limit: int = 50, offset: int = 0, username: str = None
    ) -> list[SessionModel]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if username:
                self._execute(
                    cursor,
                    """
                    SELECT session_id, title, messages, created_at, updated_at, username
                    FROM sessions WHERE username = ?
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?
                    """,
                    (username, limit, offset),
                )
            else:
                self._execute(
                    cursor,
                    """
                    SELECT session_id, title, messages, created_at, updated_at, username
                    FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?
                    """,
                    (limit, offset),
                )
            rows = cursor.fetchall()

        sessions = []
        for row in rows:
            sessions.append(SessionModel(
                session_id=row["session_id"],
                title=row["title"],
                messages=json.loads(row["messages"]) if row["messages"] else [],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                username=row.get("username", "") if isinstance(row, dict) else "",
            ))
        return sessions

    def update_session(self, session_data: SessionModel):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                UPDATE sessions SET title=?, messages=?, updated_at=?, username=? WHERE session_id=?
                """,
                (
                    session_data.title,
                    json.dumps(session_data.messages, ensure_ascii=False),
                    session_data.updated_at,
                    session_data.username,
                    session_data.session_id,
                ),
            )

    def delete_session(self, session_id: str) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "DELETE FROM sessions WHERE session_id=?", (session_id,))
            return cursor.rowcount > 0

    def add_message(self, session_id: str, message: dict):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            session = self.get_session(session_id)
            if session:
                session.messages.append(message)
                session.updated_at = datetime.now().isoformat()
                self.update_session(session)

    def get_messages(self, session_id: str) -> list[dict]:
        session = self.get_session(session_id)
        return session.messages if session else []

    def count_sessions(self, username: str = None) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if username:
                self._execute(cursor, "SELECT COUNT(*) FROM sessions WHERE username=?", (username,))
            else:
                self._execute(cursor, "SELECT COUNT(*) FROM sessions")
            row = cursor.fetchone()
            return row[0] if row else 0

    def update_session_title(self, session_id: str, title: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "UPDATE sessions SET title=?, updated_at=? WHERE session_id=?",
                (title, datetime.now().isoformat(), session_id),
            )

    def record_tool_call(
        self,
        session_id: str,
        message_id: str,
        tool_name: str,
        tool_call_id: str,
        arguments: dict,
        result: str = None,
        success: bool = True,
        duration: float = None,
        step: int = 0,
    ):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO tool_call_records (session_id, message_id, tool_name, tool_call_id, arguments, result, success, duration, step, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    message_id,
                    tool_name,
                    tool_call_id,
                    json.dumps(arguments, ensure_ascii=False),
                    result,
                    1 if success else 0,
                    duration,
                    step,
                    datetime.now().isoformat(),
                ),
            )

    def get_tool_calls(self, session_id: str) -> list[dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "SELECT * FROM tool_call_records WHERE session_id=? ORDER BY created_at",
                (session_id,),
            )
            rows = cursor.fetchall()
        results = []
        for row in rows:
            d = dict(row)
            if d.get('arguments'):
                try:
                    d['arguments'] = json.loads(d['arguments'])
                except:
                    pass
            results.append(d)
        return results

    def record_thinking(
        self,
        session_id: str,
        message_id: str,
        step: int,
        content: str,
        duration: float = None,
    ):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO thinking_records (session_id, message_id, step, content, duration, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    message_id,
                    step,
                    content,
                    duration,
                    datetime.now().isoformat(),
                ),
            )

    def get_thinkings(self, session_id: str) -> list[dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "SELECT * FROM thinking_records WHERE session_id=? ORDER BY step, created_at",
                (session_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def create_user(self, user_data: UserModel) -> UserModel:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO users (user_id, username, password_hash, organization_id, email, bound_ip, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_data.user_id,
                    user_data.username,
                    user_data.password_hash,
                    user_data.organization_id,
                    user_data.email,
                    user_data.bound_ip,
                    user_data.created_at,
                    user_data.updated_at,
                ),
            )
        return user_data

    def get_user_by_username(self, username: str) -> Optional[UserModel]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "SELECT * FROM users WHERE username=?", (username,))
            row = cursor.fetchone()
        if row is None:
            return None
        return UserModel(
            user_id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            organization_id=row.get("organization_id", ""),
            email=row.get("email", ""),
            bound_ip=row.get("bound_ip", ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_user_by_id(self, user_id: str) -> Optional[UserModel]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "SELECT * FROM users WHERE user_id=?", (user_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        return UserModel(
            user_id=row["user_id"],
            username=row["username"],
            password_hash=row["password_hash"],
            organization_id=row.get("organization_id", ""),
            email=row.get("email", ""),
            bound_ip=row.get("bound_ip", ""),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_user(self, user_data: UserModel):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                UPDATE users SET username=?, password_hash=?, organization_id=?, email=?, bound_ip=?, updated_at=?
                WHERE user_id=?
                """,
                (
                    user_data.username,
                    user_data.password_hash,
                    user_data.organization_id,
                    user_data.email,
                    user_data.bound_ip,
                    datetime.now().isoformat(),
                    user_data.user_id,
                ),
            )

    def bind_user_ip(self, username: str, ip: str) -> bool:
        """绑定用户IP地址"""
        user = self.get_user_by_username(username)
        if not user:
            return False
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "UPDATE users SET bound_ip=?, updated_at=? WHERE username=?",
                (ip, datetime.now().isoformat(), username),
            )
            return cursor.rowcount > 0

    def get_user_bound_ip(self, username: str) -> str:
        """获取用户绑定的IP地址"""
        user = self.get_user_by_username(username)
        if not user:
            return ""
        return user.bound_ip

    def register_user(self, username: str, password: str, email: str = "") -> Optional[UserModel]:
        """注册新用户"""
        existing = self.get_user_by_username(username)
        if existing:
            return None

        now = datetime.now().isoformat()
        password_hash = hash_password(password)
        user = UserModel(
            user_id=str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            organization_id="",
            email=email or "",
            bound_ip="",
            created_at=now,
            updated_at=now,
        )
        self.create_user(user)
        return user

    def verify_user_password(self, username: str, password: str) -> Optional[UserModel]:
        """验证用户密码"""
        user = self.get_user_by_username(username)
        if not user:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def update_user_password(self, username: str, new_password_hash: str) -> bool:
        """更新用户密码"""
        user = self.get_user_by_username(username)
        if not user:
            return False

        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
                (new_password_hash, datetime.now().isoformat(), username),
            )
            return cursor.rowcount > 0

    def delete_user(self, username: str) -> bool:
        """删除用户"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "DELETE FROM users WHERE username=?", (username,))
            return cursor.rowcount > 0

    def get_or_create_default_user(self) -> UserModel:
        """获取或创建默认用户"""
        default_user = self.get_user_by_username("default")
        if default_user:
            return default_user

        now = datetime.now().isoformat()
        user = UserModel(
            user_id=str(uuid.uuid4()),
            username="default",
            password_hash="",
            organization_id="",
            email="",
            bound_ip="",
            created_at=now,
            updated_at=now,
        )
        self.create_user(user)
        return user

    def list_users(self, limit: int = 50, offset: int = 0) -> list[UserModel]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = cursor.fetchall()
        return [
            UserModel(
                user_id=row["user_id"],
                username=row["username"],
                password_hash=row["password_hash"],
                organization_id=row.get("organization_id", ""),
                email=row.get("email", ""),
                bound_ip=row.get("bound_ip", ""),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def add_session_file(
        self, session_id: str, filename: str, file_path: str, file_type: str, size: int, username: str = ""
    ):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO session_files (session_id, filename, file_path, file_type, size, uploaded_at, username)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, filename, file_path, file_type, size, datetime.now().isoformat(), username),
            )

    def get_session_files(self, session_id: str) -> list[dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "SELECT * FROM session_files WHERE session_id=? ORDER BY uploaded_at DESC",
                (session_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]

    def delete_file(self, file_id: int) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(cursor, "DELETE FROM session_files WHERE id=?", (file_id,))
            return cursor.rowcount > 0

    def add_generated_file(
        self, session_id: str, message_id: str, filename: str, file_path: str, file_type: str, size: int
    ):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                """
                INSERT INTO generated_files (session_id, message_id, filename, file_path, file_type, size, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, message_id, filename, file_path, file_type, size, datetime.now().isoformat()),
            )

    def get_generated_files(self, session_id: str) -> list[dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            self._execute(
                cursor,
                "SELECT * FROM generated_files WHERE session_id=? ORDER BY created_at DESC",
                (session_id,),
            )
            rows = cursor.fetchall()
        return [dict(row) for row in rows]


_db_instance = None


def init_database(db_config: dict = None) -> Database:
    global _db_instance
    db = Database(db_config)
    db.init_tables()
    _db_instance = db
    logger.info("✅ 数据库初始化完成")
    return db


def get_database() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = init_database()
    return _db_instance
