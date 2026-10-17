"""进程内运行期 Agent 配置的只读持有者（打破 config -> services 反向依赖）。

`config.py` 的目录辅助方法（get_user_workspace_dir 等）需要“运行期加载的配置”，
但直接 import `services.agent_manager` 会让基础层依赖服务层并形成循环导入。
这里放一个最小、无依赖的持有者：

    agent_manager.init_agent_config()  -> set_runtime_agent_config({...})
    config.py / utils                -> get_runtime_agent_config()

依赖方向：config -> runtime_config <- agent_manager（单向，无环）。
"""

from __future__ import annotations

from typing import Any, Optional

_runtime_agent_config: Optional[dict[str, Any]] = None


def set_runtime_agent_config(config: dict[str, Any] | None) -> None:
    """由 agent_manager 在启动/重载时写入。"""
    global _runtime_agent_config
    _runtime_agent_config = config


def get_runtime_agent_config() -> Optional[dict[str, Any]]:
    """返回当前运行期配置；未初始化时返回 None。"""
    return _runtime_agent_config


def reset_runtime_agent_config() -> None:
    """测试用：清空运行期配置。"""
    global _runtime_agent_config
    _runtime_agent_config = None


_agent_invalidator = None


def set_agent_invalidator(fn) -> None:
    """由 services.agent_manager 注册“按用户驱逐 Agent 缓存”的实现。

    tools.expert_writer 等下层模块通过这里调用，避免 tools -> services 的
    反向 import（ARCH-03 Phase 1）。
    """
    global _agent_invalidator
    _agent_invalidator = fn


def invalidate_user_agents(username: str) -> int:
    """按用户驱逐 Agent 缓存；未注册实现时返回 0（不报错）。"""
    if _agent_invalidator is None:
        return 0
    return int(_agent_invalidator(username) or 0)
