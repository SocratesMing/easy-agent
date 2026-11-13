# 聊天行为契约

生效基线：2026-11-12。本文记录当前行为，统一金融限制、Token 用量与错误展示的维护口径。历史设计保留原有日期和撤回状态；今后调整这些行为时，应同时更新本契约、对应接口文档与回归断言，并说明变更范围和原因。

## 普通聊天与专家预检

普通聊天不进行金融意图分类，不因话题非金融而固定拒答；诗歌、旅游、编程、金融和文档制作均进入原有回答流程。技能归属、启停校验、专家职责预检及现有鉴权、工具权限继续生效。普通聊天门禁于 2026-11-11 撤回，当前源码无法证明其完整产品决策背景，本文不补写推测原因。

对应回归：`tests/api/test_chat_turn_prep.py` 覆盖不同话题、技能选择、仅附件输入、专家拒答及用量身份复用，并断言普通聊天准备阶段不创建分类模型。历史细节见 [已撤回的金融过滤设计](design/意图识别-只回答金融相关的问题.md)。

## Token 的两种范围

| 展示 / 数据 | 当前定义 |
| --- | --- |
| 聊天 `token_usage` / `done.usage` 中的 Token 字段 | 最近一次模型调用的快照；后续调用覆盖，不累加整轮调用 |
| `context_tokens` | 最近一次调用的输入上下文规模，沿用现有缓存计数适配规则 |
| 消息 `elapsed_time` / `step_count` | 当前 assistant 消息累计；审批续跑继承已有值，等待审批的时间不计 |
| 设置中的个人 Token 统计 | 按调用 ID 幂等记录供应商显式上报的完整用量，累计 `input_tokens + output_tokens`，思考和缓存不额外相加 |

例如同一轮调用两次，分别上报 `100/10`、`200/20` 个输入 / 输出 Token：聊天最终快照为 `200/20`，个人账本累计为 `330`。删除会话不会删除个人账本；未获得完整供应商证据时不估算补记。完整采集与身份隔离规则见 [Token 用量统计](token-usage-api.md)。

对应回归：`tests/agent/test_token_usage_graph.py` 的 `test_stream_latest_call_usage_and_personal_ledger_have_distinct_scopes` 经真实图执行、流处理器及临时账本验证上述两次调用；供应商 HTTP 响应使用模拟数据。`tests/agent/test_token_usage.py`、`tests/db/test_token_usage.py`、`tests/api/test_usage.py` 与 `tests/services/test_usage_scopes.py` 覆盖采集、幂等、查询与身份范围。

## 错误展示与历史

| 层次 | 当前行为 |
| --- | --- |
| 全局 `error` 临时通知 | 5 秒后清空；新提示重新计时，组件卸载时取消计时器 |
| 消息 `error` | 随消息显示，不能由全局通知的 5 秒计时器清除；兼容旧历史记录 |
| 消息 `fallback_note` | 常驻中性中断说明；实际异常持久化保存部分回复和此字段 |
| 超时 SSE `error`（`code=timeout`） | 更新 `fallback_note`，不再新增消息红条或全局红色提示 |

普通异常的即时 SSE 提示与重新读取的历史可以采用不同展示：历史以已持久化的中断说明为准。SSE error、消息 error 与全局 error 不能视为同一个可整体删除或自动隐藏的接口。

对应回归：双前端的 `autoDismiss.test.js`、`MessageError.render.test.js`、流式错误 / 超时用例，以及 `tests/api/test_run_lifecycle.py` 验证提示计时、组件超过 5 秒仍显示错误、消息状态、真实异常编排和历史回填。Vue3 的 `chatSession.errors.test.js` 同时验证 5 秒通知和切换会话的审批恢复，防止新增同名 `watch` 覆盖已有监听。
