"""知识库模块（按前端实际调用面精简）。

基础层 core：config / models / schema / repository / operations_repository /
permissions / original_store / file_validation / chunks；业务层 service 与
HTTP 层 api；聊天集成在 chat；运行时在 runtime；上游客户端在 ragflow；
人员子包 personnel（HTTP 前缀 ``/agent/personnel``，由 ``app.py`` 显式导入，
导入即注册默认管理员策略与用户扩展字段读写）；运维脚本在 scripts。

跨模块引用统一从 ``easy_agent.knowledges.facade`` 导入。本文件只保留
``schema`` 的导入副作用：导入 knowledges 即向 ``db.schema_registry``
注册建表函数（见 ``db/schema_registry.py``）。
"""

from easy_agent.knowledges.core import schema as _schema  # noqa: F401 - 导入即注册建表函数
