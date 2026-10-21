"""流式服务包：chat / resume 的 SSE 编排（C2 拆分）。

- ``projector``：纯投影函数；
- ``persistence``：增量 / 最终落库；
- ``side_effects``：工作区 / 技能 / 记忆收尾；
- ``orchestrator``：chat / resume 编排，公开生成器 ``chat_stream_generator`` /
  ``resume_stream_generator``。

包入口不导入任何模块、也不转出名字，用到什么就从定义它的子模块导入。
"""
