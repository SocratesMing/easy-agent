"""业务数据库包：SQLite / MySQL 门面 ``Database``、schema 与版本化迁移。

按领域拆分的 SQL 在 ``repositories/``；包级导出 ``Database`` 和进程级单例入口
``init_database`` / ``get_database``。
"""

from easy_agent.db.database import Database, init_database, get_database

__all__ = ["Database", "init_database", "get_database"]
