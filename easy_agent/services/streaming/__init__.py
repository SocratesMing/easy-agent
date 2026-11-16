"""流式服务包：chat / resume 的 SSE 编排、事件持久化与运行注册表（T15 领域分包）。

- ``processor``：流式输出解析 / 工具调用累积；
- ``messages``：流式消息构造；
- ``events``：SSE 事件持久化写入器；
- ``hub``：会话级事件中枢；
- ``sse`` / ``emitter``：SSE 事件契约与发送器；
- ``approvals``：HITL 审批状态；
- ``runs``：流式运行租约注册表；
- ``projector``：纯投影函数；
- ``persistence``：增量 / 最终落库；
- ``side_effects``：工作区 / 技能 / 记忆收尾；
- ``orchestrator``：chat / resume 编排，公开生成器 ``chat_stream_generator`` /
  ``resume_stream_generator``。

包入口不导入任何模块、也不转出名字，用到什么就从定义它的子模块导入。
"""
