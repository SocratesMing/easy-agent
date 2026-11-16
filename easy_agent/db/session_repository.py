"""兼容旧导入路径（T3）：``SessionRepository`` 已收敛到 ``db/repositories/``。

新代码请从 :mod:`easy_agent.db.repositories.session_repository` 导入；本文件只保留
一行 re-export，避免既有调用方 / 测试的导入路径断裂（外部依赖 override 仍指向同一
对象）。T3 会话域迁移完成后本文件可以在下个版本删除。
"""

from easy_agent.db.repositories.session_repository import SessionRepository

__all__ = ["SessionRepository"]