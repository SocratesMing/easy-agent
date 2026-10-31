"""业务服务包：会话 Agent 缓存、连接器、SSE 流式编排、流事件、记忆、MCP 与定时任务等。

包入口不导入任何子模块：导入 services 下任何模块都会先执行这里，在这里导入会让
``services.health`` 这样的小模块也连带加载 ``agent_manager`` 和 deepagents。请直接从子模块
导入，例如 ``easy_agent.services.agent_manager.get_agent_config``
（docs/design/2026-10-20-services-subpackages-and-layering.md §7）。
"""
