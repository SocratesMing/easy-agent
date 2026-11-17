"""专家中文名唯一性校验（新建 / 修改共用）。

把「自定义专家查重」与「预置专家查重」收拢到一处，避免 HTTP 接口
(``api/expert.py``) 与对话工具 (``tools/expert_writer.py``) 两条路径各自实现、
口径漂移。两条路径目前都会调用本模块的 :func:`find_name_conflict`。

查重口径（与历史实现保持一致，避免误伤存量数据）：

- 比较时**忽略全部空白 + 忽略大小写**（见 ``db.expert_store.normalize_name_cn``）；
- 自定义专家：**仅本用户**、**不含已软删除（disabled）**；“别人同名”仍允许；
- 预置专家：全局只读、跨用户共享，命中即冲突；预置专家不可被用户修改，
  因此不存在「排除自身」的需要——修改场景的 ``exclude_agent_id`` 只作用于
  自定义专家查询。

性能：自定义专家走**一次**带 ``user_id`` 索引的 SQL（``find_by_name_cn``），
预置专家只读各目录 ``config.yaml`` 的名称字段（不读提示词正文），无循环查库、
无 N+1。任一命中立即返回，不再做后续查询。
"""

from typing import TYPE_CHECKING, Optional

from easy_agent.services.experts.public import find_public_expert_by_name_cn

if TYPE_CHECKING:  # 仅类型标注用，避免导入期环
    from easy_agent.db.repositories.expert_repository import ExpertRepository


def find_name_conflict(
    store: "ExpertRepository",
    user_id: str,
    name_cn: str,
    *,
    exclude_agent_id: str = "",
) -> Optional[dict]:
    """返回第一个重名专家的信息；无冲突返回 ``None``。

    Args:
        store: 专家仓储（提供 ``find_by_name_cn``）。
        user_id: 归属用户 id（自定义专家按此隔离；预置专家全局共享）。
        name_cn: 待校验的中文名。
        exclude_agent_id: 修改场景传入自身 ID，避免「和自己重名」——只作用于
            自定义专家查询；预置专家不可修改，不存在自身项。

    Returns:
        命中的专家 dict（至少含 ``export_name_cn``，预置项另含 ``scope``），
        供调用方拼接可读错误文案；无冲突返回 ``None``。
    """
    # 1) 本用户自定义专家：一次索引 SQL（user_id + agent_status），库内已按
    #    normalize_name_cn 口径比对并排除软删除 / 排除自身。
    conflict = store.find_by_name_cn(
        user_id, name_cn, exclude_agent_id=exclude_agent_id
    )
    if conflict:
        return conflict

    # 2) 预置专家：只读 config.yaml 的名称字段。与自定义专家 ID 空间不重叠
    #    （preset_* vs expert_*），故无需（也无法）用 exclude_agent_id 排除自身。
    return find_public_expert_by_name_cn(name_cn)


__all__ = ["find_name_conflict"]
