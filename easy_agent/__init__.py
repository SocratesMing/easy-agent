"""Easy Agent - AI智能体框架"""

__version__ = "0.1.0"

from .agent import EasyAgent
from .config import Config
from .model import create_model
from .skills import discover_skills

__all__ = [
    "EasyAgent",
    "Config",
    "create_model",
    "discover_skills",
]
