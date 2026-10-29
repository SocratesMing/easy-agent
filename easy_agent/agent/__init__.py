"""Agent package: public facade for the EasyAgent runtime.

Canonical modules:
- ``core``: EasyAgent + graph assembly
- ``backend``: CompositeBackend construction
- ``workspace``: memory/skills/permissions/workspace helpers
- ``middleware``: middleware stack
- ``hitl``/``skills``/``subagents``/``summarization``/``model``/``graph_cache``

Old ``easy_agent.agent`` imports keep working through this re-export facade.
"""

from .core import *  # noqa: F401,F403 - compatibility facade
from .core import (  # noqa: F401
    EasyAgent,
    _shared_graph_checkpointer,
    build_subagent_specs,
)
from .core import _HITL_WHEN_PREDICATES, _PathTranslatingShell  # noqa: F401
from .backend import _build_backend_for_context  # noqa: F401
from .middleware import build_agent_middleware  # noqa: F401
from .workspace import LONG_TERM_MEMORY_VIRTUAL_PATH, AgentWorkspaceMixin  # noqa: F401
from easy_agent.persistence.checkpoints import release_session_checkpointer  # noqa: F401

__all__ = [
    "AgentWorkspaceMixin",
    "EasyAgent",
    "LONG_TERM_MEMORY_VIRTUAL_PATH",
    "_HITL_WHEN_PREDICATES",
    "_PathTranslatingShell",
    "_build_backend_for_context",
    "release_session_checkpointer",
    "build_agent_middleware",
    "build_subagent_specs",
]
