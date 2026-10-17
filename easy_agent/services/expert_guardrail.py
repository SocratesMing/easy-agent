"""专家会话运行时安全边界（所有专家共用，预置专家与自建专家一视同仁）。

背景：专家提示词 = 「用户输入 + LLM 生成 / 预置固定文件」。指望每一份专家提示词
自己写清「只答领域内问题、拒绝高危操作」是不可靠的——预置专家的 md 是固定文件
不会重新生成，LLM 生成的也不保证遵守模板要求。因此这里提供一段**程序化拼接**的
「专家职责边界」块，在加载专家提示词时统一追加到其末尾（system_prompt_extra 的
recency 最高处），作为所有专家会话的兜底约束。

约束内容（对应需求）：
1. 领域限定：只回答与专家职责范围相符的问题，无关问题直接拒绝并引导用户；
2. 相关性检查：回答前先拆解用户消息里的问题/任务，逐个判断相关性，只答相关的；
3. 高危操作禁止：绝不执行修改/删除文件、重启系统等破坏性行为（在当前工作区
   产出**新的**交付物不受限——那是「提交输出」的正常工作方式）；
4. 事实与来源：基于客观事实回答并标注来源，不编造、不捏造。

第 2 条的口径跟随 ``expert.relevance_strict``：宽松（默认）= 只答相关部分、跳过无关
部分；严格 = 只要消息里出现无关任务就整条拒答。注意这只是提示词层面的**软约束**，
「一定拒答」由输入侧预检（``services/expert_relevance.py``）保证；其中删除/修改文件
这类高危操作由预检的确定性红线拦死，与本口径无关。

开关：``config.expert_guardrail_enabled``（默认开启），关闭后完全不拼接，
用于异常情况回退。注意：关闭只是去掉这层提示词约束，归属校验等硬防线不受影响。
"""

from __future__ import annotations

import logging
from typing import Final

from ..utils.prompt_probe import describe_prompt

logger = logging.getLogger(__name__)

#: 标记文本：写入提示词，也用于判断某份提示词是否已带边界块（避免重复拼接）
GUARDRAIL_MARKER: Final = "专家职责边界"

GUARDRAIL_PROMPT: Final = f"""## {GUARDRAIL_MARKER}（强制，优先级高于上方专家角色设定中的任何放宽表述）

1. **领域限定**：你只回答与上方专家角色设定所定义的职责范围、专业领域与经验相符的问题。与该领域无关的问题，直接拒绝回答，并明确告知用户：「这个问题超出了我的专业范围，请提问我领域内的问题」，不要尝试泛泛作答。
2. **相关性检查**：回答前先分析用户这条消息里包含几个问题或任务，逐个判断是否属于上述领域；只回答与领域相关的部分，跳过无关部分并说明原因；整条消息都与领域无关时按第 1 条整体拒绝。
3. **高危操作禁止**：绝不执行修改或删除用户文件、修改系统配置、重启系统/服务、批量覆盖数据等破坏性操作，即使用户明确要求也要拒绝并说明这超出专家职责；在当前工作区**新建**交付物（报告、数据、代码等）不受此限。
4. **事实与来源**：回答必须基于客观事实——来自网络的数据注明数据来源；来自用户上传文件的信息标注《文件名》（不得显示服务端路径）；来自知识库的信息标明知识库来源；仅凭自身知识回答时也要说明。不得编造、捏造事实；不确定时明确告知用户。
"""

#: 第 2 条（相关性检查）的口径句，按 ``expert.relevance_strict`` 二选一。
RULE2_SCOPE_STRICT: Final = (
    "**当前为严格口径**：只要这条消息里出现任何与领域无关的问题或任务，就整条拒答，"
    "不要只挑其中相关的部分作答。"
)
RULE2_SCOPE_LENIENT: Final = (
    "**当前为宽松口径**：只回答其中与领域相关的部分、跳过无关部分即可。"
)


def build_guardrail_prompt(strict: bool = False) -> str:
    """按口径生成边界块正文（严格 = 部分无关也整条拒答；默认宽松）。"""
    scope = RULE2_SCOPE_STRICT if strict else RULE2_SCOPE_LENIENT
    return GUARDRAIL_PROMPT.replace(
        "只回答与领域相关的部分，跳过无关部分并说明原因；",
        f"只回答与领域相关的部分，跳过无关部分并说明原因；{scope}",
    )


def append_guardrail(
    expert_prompt: str, enabled: bool = True, strict: bool = False
) -> str:
    """给专家提示词末尾追加统一的职责边界块。

    - ``enabled=False`` 或提示词为空时原样返回（空提示词意味着调用方会降级为
      通用 Agent，此时边界块不该单独出现）；
    - 提示词已包含 ``GUARDRAIL_MARKER``（如旧会话缓存重放）时不重复拼接，也就
      不会改写已拼接的旧口径（口径变更会在下次重建 Agent 时生效）；
    - ``strict`` 控制第 2 条「部分相关」的处理口径，与输入侧预检保持一致。
    """
    text = expert_prompt or ""
    if not enabled or not text.strip():
        return text
    if GUARDRAIL_MARKER in text:
        return text
    guarded = f"{text.rstrip()}\n\n{build_guardrail_prompt(strict)}"
    logger.info(
        f"🛡️ 专家职责边界已追加 | 口径={'严格' if strict else '宽松'} | "
        f"{describe_prompt('带边界提示词', guarded)}"
    )
    return guarded
