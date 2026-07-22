from .chat import router as chat_router
from .sessions import router as sessions_router
from .files import router as files_router
from .auth import router as auth_router
from .bloom import bloom_router
from .forex import forex_router
import platform

from .prompts import prompts_router
from .settings import router as settings_router
from .skill_center import router as skill_center_router
from .scheduled_tasks import router as scheduled_tasks_router

# Web Terminal 依赖 pty（POSIX 专用），Windows 不支持，故不加载该模块
if platform.system() != "Windows":
    from .terminal import router as terminal_router

__all__ = [
    "chat_router",
    "sessions_router",
    "files_router",
    "auth_router",
    "bloom_router",
    "forex_router",
    "prompts_router",
    "settings_router",
    "skill_center_router",
    "scheduled_tasks_router",
]

if platform.system() != "Windows":
    __all__.append("terminal_router")
