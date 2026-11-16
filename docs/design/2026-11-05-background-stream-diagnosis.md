# 会话后台运行、前台不可见：修复与验证

日期：2026-11-05。已修复本地稳定复现的断流问题，覆盖 Vue2 / Vue3 主页面与侧栏。未提交 Git。

## 原因与修复

原故障链：**SSE 提前结束但没有业务终态 → 前端 finally 清除运行状态和停止按钮 → 后端继续执行 → 再次提问返回 409。**

- EOF / 网络读取异常后保留运行状态和已有内容，显示重连提示，通过状态查询及 `/agent/chat/stream/live` 恢复；不重发原消息 POST。
- 回放到达后恢复本轮初始快照再重建，避免正文、思考、工具卡片和用量重复，兼容审批续跑。
- 停止或卸载会中止重连及等待，清除重连提示；401 / 403 / 404 结束重试。完成、业务错误和等待审批保持原有终态处理。
- Vue2 主页面会覆盖 mixin 的同名处理器，已抽至 `pageChunkHandler.js` 并接入恢复，保留知识证据与工具参数增量行为。

## 涉及文件

| 用途 | 文件 |
| --- | --- |
| 共用恢复逻辑 | [chatStreamRecovery.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-shared/utils/chatStreamRecovery.js) |
| Vue2 请求及鉴权 | [chat.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/api/chat.js)、[request.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/utils/request.js) |
| Vue2 主页面及处理器 | [EasyAgentPage.vue](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/EasyAgentPage.vue)、[pageChunkHandler.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/pageChunkHandler.js) |
| Vue2 侧栏及同步副本 | [chunkHandler.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.js)、[chatStreamRecovery.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/shared/utils/chatStreamRecovery.js) |
| Vue3 请求及鉴权 | [auth.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/api/auth.js)、[chat.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/api/chat.js) |
| Vue3 主页面、侧栏及同步副本 | [useStreamChunks.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/composables/useStreamChunks.js)、[chunkHandler.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/services/chunkHandler.js)、[chatStreamRecovery.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/shared/utils/chatStreamRecovery.js) |
| 回归测试 | [chatStreamRecovery.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/api/chatStreamRecovery.test.js)、[streamReplay.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/streamReplay.test.js)、[chatStreamRecovery.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/api/chatStreamRecovery.test.js)、[streamReplay.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/services/streamReplay.test.js)、[useStreamChunks.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/composables/useStreamChunks.test.js) |

共享副本由 `node scripts/dev/sync-frontend-shared.mjs` 生成。后端无本次修复改动。

## 已执行验证

使用真实页面、FastAPI、DeepAgents 和临时数据库；模型替换为本地受控流服务。修复前相同故障连续复现 3 次。

| 验证 | 结果 |
| --- | --- |
| Vue2 / Vue3 各连续提前结束 SSE 3 次 | 6 次均恢复；停止按钮保留、正文无重复；每轮原 POST 仅 1 次 |
| 两套页面恢复后点击停止 | 后台 task 结束、模型连接被取消、流锁释放 |
| 两套页面状态接口注入 503，再点击停止 | 部分内容及停止入口保留；后台结束，取消后不再重连 |
| 两套页面重连后正常完成、再次提问 | 最终内容无重复；后台结束；再次发送 HTTP 200 |
| Vue2 刷新恢复后停止 | 回放和取消正常 |
| 前端专项回归 / 后端流相关测试 | 48 / 24 项通过 |
| 两套前端构建、Vue2 兼容及模块检查、共享同步检查 | 通过（构建有既有体积/依赖提示） |
| 前端全量测试 | Vue2：201 通过、1 失败；Vue3：83 通过、1 失败 |

全量失败均为本次未改动文件的既有行数预算：Vue2 `chatSession.js` 1209 > 1205；Vue3 `FilePreview.vue` 1130 > 1128。Vue3 原 `npm run test:unit` 另有空 glob 问题，本次通过枚举所有测试文件执行。

证据：[修复验收摘要](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/fix-summary.json)；[修复前三次复现](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/summary.json)。

## 自己怎么验证

1. 按[隔离环境运行步骤](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/README.md)启动测试服务并注册测试账号，发送持续分析金融风险的消息，等待第 3 组输出。
2. 执行 `curl -X POST 'http://127.0.0.1:18102/__fault/drop?mode=eof'`，明确切断实际 SSE；普通浏览器 Offline 在本地不保证切断既有连接。
3. 确认停止按钮一直保留，内容自动继续且第 1～3 组不重复；再重复断流两次。
4. 点击停止，检查测试端点 `/__repro/state?session_id=当前会话ID` 的 `local_task_running=false`，随后同一会话应能再次提问。

自动验收脚本：[fixed-case.pw.js](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/fixed-case.pw.js)、[reconnect-stop.pw.js](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/reconnect-stop.pw.js)、[completion-case.pw.js](/Users/lulongfei/Desktop/agent/easy-agent/output/playwright/background-stream-repro/completion-case.pw.js)。

边界：本次修复已复现的“断流导致停止入口丢失”。截图现场是否同因仍需现场日志；跨实例取消、任务注册前取消和首个 start 前断线，未包含在本次修复中。

建议提交信息：`fix(chat): 修复流式断连后过程丢失及停止入口消失`
