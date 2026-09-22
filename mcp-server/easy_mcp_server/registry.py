"""业务清单注册表：把本模块提供的业务发布到共享库 ``mcp_businesses``。

这是 mcp-server 唯一**写**共享库的地方，存在的理由只有一个：让"新增业务"
成为纯粹的模块内部改动。加完 ``businesses/<name>/`` 重启服务，主应用设置页
就能看到该业务并签发 Key，主应用侧零改动。

失败只告警、不阻断启动：注册表是"发现通道"而非运行必需，主应用读不到时
会回退到内置兜底清单（见 ``easy_agent/services/mcp_api_keys.py``）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable

from .contract import BUSINESS_REGISTRY_TABLE, business_registry_table_sql
from .db import connection

logger = logging.getLogger("easy-mcp-server")

# 只刷新 updated_at：created_at 记录该业务首次出现的时间，重启动不该被改写
_UPSERT = f"""
INSERT INTO {BUSINESS_REGISTRY_TABLE} (name, created_at, updated_at)
VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)
"""


def publish(names: Iterable[str]) -> int:
    """登记/刷新业务清单，返回写入条数；任何异常都只告警。

    Args:
        names: 业务名列表（通常来自 ``contract.business_names()``）。
    """
    items = list(names)
    if not items:
        return 0

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with connection() as conn:
            with conn.cursor() as cursor:
                # 自愈：拿到的是一个从未跑过主应用初始化的库也能工作
                cursor.execute(business_registry_table_sql())
                for name in items:
                    cursor.execute(_UPSERT, (name, now, now))
    except Exception as e:
        logger.warning(f"[mcp-server] 业务注册表写入失败（不影响服务）: {e}")
        return 0

    logger.info(f"[mcp-server] 业务注册表已更新: {', '.join(items)}")
    return len(items)
