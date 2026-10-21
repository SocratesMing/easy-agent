"""HTTP 路由包：汇总各业务 router，供 app.py 统一注册。"""

from easy_agent.api.chat import router as chat_router
from easy_agent.api.sessions import router as sessions_router
from easy_agent.api.files import router as files_router
from easy_agent.api.auth import router as auth_router
from easy_agent.api.completion import router as completion_router
import platform

from easy_agent.api.prompts import prompts_router
from easy_agent.api.settings import router as settings_router
from easy_agent.api.skill_center import router as skill_center_router
from easy_agent.api.connectors import router as connectors_router
from easy_agent.api.scheduled_tasks import router as scheduled_tasks_router
from easy_agent.api.expert import router as expert_router

# Web Terminal 依赖 pty（POSIX 专用），Windows 不支持，故不加载该模块
if platform.system() != "Windows":
    from easy_agent.api.terminal import router as terminal_router

__all__ = [
    "chat_router",
    "sessions_router",
    "files_router",
    "auth_router",
    "completion_router",
    "prompts_router",
    "settings_router",
    "skill_center_router",
    "connectors_router",
    "scheduled_tasks_router",
    "expert_router",
]

if platform.system() != "Windows":
    __all__.append("terminal_router")
