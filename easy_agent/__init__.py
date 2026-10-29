"""Easy Agent - AI智能体框架"""

__version__ = "0.1.0"

from easy_agent.agent import EasyAgent
from easy_agent.bootstrap.config import Config
from easy_agent.agent.model import create_model
from easy_agent.agent.skills import discover_skills

__all__ = [
    "EasyAgent",
    "Config",
    "create_model",
    "discover_skills",
]
