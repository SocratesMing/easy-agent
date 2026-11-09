"""记忆什么时候整理：这一轮有该记的信号就当轮整理，否则攒够几轮再整理一次。

改造前会话记忆和长期记忆每轮回答后都各调一次模型整份重写。真实数据：54 个用户里 34 个
的长期记忆一直是空的；10-22 这一天 46 轮对话触发了 46 次长期记忆更新（中位 2.6 秒，最长
48 秒），更新完长度只有 8~47 字——绝大多数轮次根本没有该记的内容。``logs/sessions`` 里 51 条
真实提问，下面两组信号规则一条都没命中。

- **长期记忆**（跨会话的用户画像：工作背景、个人背景、当前关注、近期动态）：用户明说的
  （「我是…」「以后都…」「记住…」）当轮整理；其余要从多轮任务里归纳（一直在问利率债 →
  当前关注利率债），攒够 :data:`EVERY_N_TURNS` 轮整理一次。按用户累计，跨会话。
- **会话记忆**（本会话的决策与理由、偏好与约定）：用户给了决定、约束或纠正（「就用…」
  「以后都…」「不对，应该…」），或者本轮有操作被用户拒绝，当轮整理；否则攒够
  :data:`SESSION_EVERY_N_TURNS` 轮整理一次。会话线程里完整历史本来就在请求中，晚几轮写进
  会话记忆不影响后续轮次；会话记忆真正起作用是在历史被压缩之后。

信号规则宁可多报：误报只是多调一次模型，漏报的内容攒够轮数时也会补上。整理时把攒下的轮次
一起交给模型。攒下的轮次只在进程内；进程重启会丢掉还没整理的几轮，明说的内容当轮就整理，
不受影响。
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

#: 长期记忆：没有画像信号时，攒够多少轮（含本轮）整理一次
EVERY_N_TURNS = 5
#: 长期记忆：登记的每轮用户输入最多保留多少字（只用来归纳关注点，不需要全文）
MAX_PENDING_CHARS = 300
#: 会话记忆：没有决策 / 约束 / 纠正信号时，攒够多少轮（含本轮）整理一次
SESSION_EVERY_N_TURNS = 3
#: 会话记忆：登记的每轮摘要（用户输入 + 回复节选 + 操作）最多保留多少字
SESSION_NOTE_CHARS = 900
#: 最多同时攒多少份记忆的轮次；会话很多时淘汰最久没动的（多为早已结束的短会话）
MAX_PENDING_KEYS = 2000


def _patterns(*sources: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(source, re.IGNORECASE) for source in sources)


_LONG_TERM_SIGNALS = _patterns(
    # 明确要求记住
    r"记住|记一下|帮我记|别忘了|记得",
    # 长期偏好、习惯、默认做法
    r"以后|今后|往后|默认|一律|每次都|总是|不要再|别再",
    r"我(?:更|比较|一般)?(?:喜欢|偏好|习惯|倾向)|偏好",
    # 身份与工作背景
    r"我是|本人|我(?:在|就职于).{0,8}(?:公司|银行|基金|证券|券商|集团|团队|部门|单位|学校|大学|研究所)",
    r"我们(?:公司|团队|部门|小?组|行里|所里|机构)|我负责|我从事|我的(?:工作|岗位|职位|职责|团队|项目|方向)",
    # 当前关注与近期计划
    r"最近(?:在|一直)|正在(?:做|准备|研究|写)|在准备|这(?:周|个月)要|下(?:周|个月)要|接下来要|我的目标",
    r"\bremember\b|\bi(?:'m| am)\b|\bmy (?:role|job|team|name)\b|\bi work\b",
)

_SESSION_SIGNALS = _patterns(
    # 明确要求记住、长期约定
    r"记住|记一下|别忘了|以后|今后|往后|默认|一律|统一|总是|每次都",
    # 约束与规范
    r"约定|规定|规范|必须|禁止|不允许|不要再|别再|只能|只用|不能用",
    # 拍板的决定
    r"就用|改用|换成|改成|决定|选定|确定用|采用|选用|就按|按这个",
    # 纠正
    r"不对|错了|搞错|应该是|不是这样|重新来|返工",
    r"我(?:更|比较|一般)?(?:喜欢|偏好|习惯)|偏好",
    r"\bremember\b|\bfrom now on\b|\balways\b|\bnever\b|\bdon't\b|\binstead\b",
)

#: 本轮操作摘要里「用户拒绝」的标记（``services/memory/digest.py``）：用户否掉了一步，是该记的约束
REJECTED_ACTION_MARK = "✗ 用户拒绝"


def has_long_term_signal(text: str) -> bool:
    """用户这句话里是否可能有该进长期记忆的画像信息（偏好、身份、关注点、明确要求记住）。"""
    return any(pattern.search(text or "") for pattern in _LONG_TERM_SIGNALS)


def has_session_signal(user_message: str, turn_actions: str = "") -> bool:
    """这一轮是否可能有该进会话记忆的内容：用户给了决定 / 约束 / 纠正，或有操作被用户拒绝。"""
    if REJECTED_ACTION_MARK in (turn_actions or ""):
        return True
    return any(pattern.search(user_message or "") for pattern in _SESSION_SIGNALS)


@dataclass(frozen=True)
class TurnDecision:
    """``TurnBatcher.add`` 的结果。"""

    #: 本轮是否整理
    due: bool
    #: 整理原因：``"signal"``（有信号）/ ``"count"``（攒够轮数）；不整理时为 ``""``
    reason: str = ""
    #: 整理时一起交给模型的、之前攒下的轮次摘要（按时间先后，不含本轮）
    earlier: tuple[str, ...] = ()
    #: 不整理时已攒的轮数（含本轮）
    pending: int = 0


class TurnBatcher:
    """按记忆（长期记忆按用户、会话记忆按会话）攒还没整理的轮次，决定本轮要不要整理。"""

    def __init__(
        self,
        *,
        every_n_turns: int,
        max_note_chars: int = MAX_PENDING_CHARS,
        max_keys: int = MAX_PENDING_KEYS,
    ) -> None:
        self.every_n_turns = max(1, int(every_n_turns))
        self.max_note_chars = max(1, int(max_note_chars))
        self.max_keys = max(1, int(max_keys))
        self._pending: OrderedDict[str, list[str]] = OrderedDict()
        self._lock = threading.Lock()

    def add(self, key: str, note: str, *, signal: bool) -> TurnDecision:
        """登记一轮（``note`` 是本轮摘要）；该整理时返回之前攒下的轮次并清空，否则只记下本轮。"""
        with self._lock:
            pending = self._pending.pop(key, [])
            if signal or len(pending) + 1 >= self.every_n_turns:
                return TurnDecision(
                    due=True, reason="signal" if signal else "count", earlier=tuple(pending)
                )
            pending.append((note or "").strip()[: self.max_note_chars])
            self._pending[key] = pending  # 重新放到末尾：最近动过的最后淘汰
            while len(self._pending) > self.max_keys:
                self._pending.popitem(last=False)
            return TurnDecision(due=False, pending=len(pending))

    def reset(self) -> None:
        """清空攒下的轮次（测试用）。"""
        with self._lock:
            self._pending.clear()


#: 进程级实例：``services/streaming/side_effects.py`` 在每轮收尾时登记
LONG_TERM_BATCHER = TurnBatcher(every_n_turns=EVERY_N_TURNS, max_note_chars=MAX_PENDING_CHARS)
SESSION_BATCHER = TurnBatcher(every_n_turns=SESSION_EVERY_N_TURNS, max_note_chars=SESSION_NOTE_CHARS)
