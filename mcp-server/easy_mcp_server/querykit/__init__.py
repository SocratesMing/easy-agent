"""MySQL 只读问数工具包（跨业务共享）。

各个问数场景（strategyqa 以及后续新增的）都需要同一套底层能力，这里集中提供，
避免每个业务包各复制一份：

- :mod:`guard`  —— SQL 只读网关：把模型生成的 SQL 收敛为单条 SELECT
- :mod:`pool`   —— 进程级 MySQL 连接池（含会话只读加固）
- :mod:`explore`—— 表/字段探索（读 information_schema）
- :mod:`config` —— 统一的环境变量读取（前缀 ``QUERYKIT_``）

业务包只需要写自己的"领域层"（查什么、怎么查、口径是什么），
通用收敛与资源管理由这里负责。
"""

from .config import (
    DEFAULT_DENY_TABLES,
    DEFAULT_MAX_ROWS,
    DEFAULT_POOL_SIZE,
    DEFAULT_TIMEOUT_MS,
    env_int,
    env_list,
    max_rows,
    pool_size,
    query_timeout_ms,
)
from .explore import ExplorePolicy, describe_table, list_tables, policy_from_env, sample_rows
from .guard import assert_identifier, assert_readonly, json_safe
from .pool import get_pool, readonly_connection, readonly_query, reset_pool

__all__ = [
    "DEFAULT_DENY_TABLES",
    "DEFAULT_MAX_ROWS",
    "DEFAULT_POOL_SIZE",
    "DEFAULT_TIMEOUT_MS",
    "ExplorePolicy",
    "assert_identifier",
    "assert_readonly",
    "describe_table",
    "env_int",
    "env_list",
    "get_pool",
    "json_safe",
    "list_tables",
    "max_rows",
    "policy_from_env",
    "pool_size",
    "query_timeout_ms",
    "readonly_connection",
    "readonly_query",
    "reset_pool",
    "sample_rows",
]
