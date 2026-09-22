"""本子项目对外契约（唯一事实来源）。

mcp-server 作为一个**独立模块**，对外只承诺四件事，全部定义在本文件：

1. **业务清单** —— ``businesses/`` 下的包名就是业务名，也是 URL 后缀
   （``businesses/strategyqa`` → ``/mcp/strategyqa/``）
2. **URL 形态** —— ``{base}/mcp/{business}/``（**必须有尾斜杠**）
3. **鉴权契约** —— ``Authorization: Bearer mcp_<token>``，库中只存 sha256；
   主应用签发、本模块只读校验
4. **数据契约** —— 共享库中的 ``mcp_api_keys``（密钥）与 ``mcp_businesses``
   （业务清单注册表）两张表

依赖方向是单向的::

    主应用 easy-agent ──读取契约（共享库两表）──▶ mcp-server

- 本模块**不 import** 主应用任何代码
- 主应用**不 import** 本模块（它只按表结构读契约，见 ``registry.py``）
- 因此新增/删除业务只改 ``businesses/`` 一个目录，重启本服务后主应用自动感知

改表结构时两侧必须同步（已记录在 README）：主应用那一份在
``easy_agent/db/database.py::_create_misc_tables``。
"""

from __future__ import annotations

import hashlib
import pkgutil
import re

from . import businesses as _businesses_pkg

# ── 契约常量 ───────────────────────────────────────────────────────────

API_KEY_PREFIX = "mcp_"
"""API Key 明文前缀，主应用签发时同样使用（便于识别与日志脱敏）。"""

API_KEY_TABLE = "mcp_api_keys"
"""密钥表名（主应用写、本模块只读）。"""

BUSINESS_REGISTRY_TABLE = "mcp_businesses"
"""业务清单注册表名（本模块写、主应用只读）。"""

MCP_PATH_PREFIX = "/mcp"
"""所有业务端点的统一前缀。"""

BUSINESS_URL_RE = re.compile(r"/mcp/([^/]+)/?$")
"""从 URL 反解业务名，与主应用设置页的识别规则保持一致。"""


# ── 业务清单 ───────────────────────────────────────────────────────────


def business_names() -> list[str]:
    """返回当前子项目提供的业务名（``businesses/`` 下的包名，已排序）。

    刻意只扫描目录、**不 import 业务包**：import 会拉起 FastMCP、连接池
    等运行时对象，而注册表发布、管理脚本只需要一份清单，不该有这些副作用。
    """
    return sorted(
        info.name for info in pkgutil.iter_modules(_businesses_pkg.__path__) if info.ispkg
    )


def business_url(base_url: str, business: str) -> str:
    """拼出某个业务的 MCP 端点。尾斜杠不能省：MCP 客户端不跟随 307。"""
    return f"{base_url.rstrip('/')}{MCP_PATH_PREFIX}/{business}/"


def business_from_url(url: str) -> str | None:
    """从 URL 反解业务名（非本契约形态返回 None）。"""
    match = BUSINESS_URL_RE.search(url or "")
    return match.group(1) if match else None


# ── 鉴权 ───────────────────────────────────────────────────────────────


def hash_api_key(api_key: str) -> str:
    """API Key 落库前的哈希。

    主应用签发时用同一算法（``hashlib.sha256`` 十六进制）——改这里等于改协议，
    两侧必须同步。
    """
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# ── 数据契约（建表语句） ────────────────────────────────────────────────
# 这些 DDL 供本模块自举使用（scripts/init_keys.py 本地开发建表、
# registry.publish() 自愈建表），主应用持有一份等价 DDL。


def _create_table(name: str, columns: str) -> str:
    return f"CREATE TABLE IF NOT EXISTS {name} (\n{columns}\n)"


def api_keys_table_sql() -> str:
    """``mcp_api_keys`` 建表语句（MySQL）。

    UNIQUE(username, business) 保证每用户每业务仅一把有效 key，重签即覆盖。
    """
    return _create_table(
        API_KEY_TABLE,
        "    id INTEGER PRIMARY KEY AUTO_INCREMENT,\n"
        "    username VARCHAR(64) NOT NULL,\n"
        "    business VARCHAR(64) NOT NULL,\n"
        "    key_hash CHAR(64) NOT NULL UNIQUE,\n"
        "    created_at VARCHAR(50) NOT NULL,\n"
        "    updated_at VARCHAR(50) NOT NULL,\n"
        "    revoked INTEGER NOT NULL DEFAULT 0,\n"
        "    UNIQUE KEY uk_mcp_api_keys_user_business (username, business)",
    )


def business_registry_table_sql() -> str:
    """``mcp_businesses`` 建表语句（MySQL）。

    本模块启动时把 ``business_names()`` 写进这张表，主应用读它来渲染设置页的
    业务下拉与 Key 签发白名单 —— 这就是"新增业务不用改主应用"的落地点。
    """
    return _create_table(
        BUSINESS_REGISTRY_TABLE,
        "    name VARCHAR(64) PRIMARY KEY,\n"
        "    created_at VARCHAR(50) NOT NULL,\n"
        "    updated_at VARCHAR(50) NOT NULL",
    )
