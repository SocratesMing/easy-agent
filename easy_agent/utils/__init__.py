"""通用工具包：密码与 JWT、文件解析、会话 / 任务日志、部署校验等。

包入口不导入任何子模块：在这里导入会让任何 ``utils`` 子模块都连带加载文件解析器等重模块。
请直接从子模块导入，例如 ``easy_agent.utils.auth.create_access_token``。会话归属校验在
``db/session_ownership.py``，分布式锁在 ``persistence/distributed_lock.py``（都依赖数据库，
不属于这一层）。
"""
