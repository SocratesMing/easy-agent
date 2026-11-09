# Increment 6 执行报告（ARCH-05 前端 SSE 统一）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 5） |
| 依据 | 审计 ARCH-05；`docs/design/2026-10-17-sse-event-contract.md` |
| 约束 | 两套前端基线同时维护；行为保持兼容；后端契约不变 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增统一 SSE 读取器 | `frontend-vue2/src/api/sse.js`、`frontend/src/api/sse.js`：唯一 `readSSE(response, onEvent, {signal})`（按行切分、跨 chunk 拼接、JSON 解析、Abort 处理、handler 抛错向上传播、`releaseLock`） | 前端 7 份重复解析器收敛为 2 个基线各 1 份 |
| Vue2 消费端收敛 | `api/chat.js`（3 处调用）、`api/expert.js`、`features/knowledge/api.js` 删除本地解析器，改用 `readSSE` | 知识库错误事件仍按原语义抛出 |
| Vue3 消费端收敛 | `api/chat.js`（attach/send/resume 3 处）、`api/expert.js` 删除 4 段内联解析器 | 保留 sendMessage 的 done 事件日志 |
| 前端单测 | 两个基线各新增 `src/api/sse.test.js` 4 项：跨 chunk/多事件、非 data 行/尾部无换行、handler 抛错传播、已 abort 不产事件 | `npm run test:unit`：Vue2 66 passed（含 SSE 4 项），Vue3 4 passed |
| npm 脚本 | Vue2 `test:unit` 纳入 `src/api/*.test.js`；Vue3 新增 `test:unit` 脚本 | 后续 CI 可执行 |
| 前端构建脚本修复 | `frontend/package.json` 改用 `bash` 调 `generate-runtime-config.sh`；脚本 CRLF 规范为 LF | 修复构建后 `set: Illegal option` / `$'\r'` 报错，构建日志干净 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| Vue2 `npm run test:unit` | 66 passed / 0 failed（含新增 SSE 4 项） |
| Vue3 `npm run test:unit` | 4 passed / 0 failed |
| `grep getReader` | 仅剩 2 个基线各自的 `api/sse.js`（唯一解析实现） |
| 构建 | `frontend-vue2` / `frontend` 的 `npm run build` 均成功（VUE2_EXIT=0 / VUE3_EXIT=0；Vue2 build complete，Vue3 Vite built in 20.2s） |
| 后端 | 本轮未改后端，回归基线仍为 936 passed |

## 3. 未完成/后续

1. ARCH-05 后端生产端尚未全部改为引用 `services/sse.py` 的常量（当前由契约测试扫描源码兜底）。
2. 前端 `docs/architecture-current.md` 中“SSE 各一套”说明需同步（本轮已更新）。
3. ARCH-08/09/15/16、ARCH-03 Phase 2/3 未开始；Batch 0/1 安全项未执行。
