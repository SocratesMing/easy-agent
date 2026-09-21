def test_knowledge_router_prefixes():
    from easy_agent.knowledge.api import router as user_router
    from easy_agent.knowledge.ops_api import router as ops_router
    assert user_router.prefix == "/agent/knowledge/v1"
    assert ops_router.prefix == "/agent/knowledge/v1/admin"


def test_knowledge_module_imports_without_ragflow():
    import easy_agent.knowledge.config  # noqa: F401
    import easy_agent.knowledge.schema  # noqa: F401
    import easy_agent.knowledge.agent_extension  # noqa: F401
