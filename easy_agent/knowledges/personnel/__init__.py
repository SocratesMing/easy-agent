"""知识库域的人员管理：账号录入/编辑、团队空间授权、Excel 导入与用户扩展字段。

归属 ``knowledges`` 是因为这些数据只服务知识库侧（团队空间、知识授权、审计），
HTTP 前缀为 ``/agent/personnel``，与知识库接口保持同一套鉴权与审计。
"""

from easy_agent.knowledges.personnel.api import router
from easy_agent.knowledges.personnel import bootstrap as _bootstrap  # noqa: F401  （注册默认管理员策略）
from easy_agent.knowledges.personnel import user_fields as _user_fields  # noqa: F401  （注册用户扩展字段读写）

__all__ = ["router"]
