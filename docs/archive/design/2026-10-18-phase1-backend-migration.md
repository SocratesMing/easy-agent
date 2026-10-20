# 多副本 Phase 1：DeepAgents 0.7 兼容与 Backend 重构

## 1. 版本窗口

| 包 | 旧 | 新 |
| --- | --- | --- |
| deepagents | `>=0.6.8,<0.7` | `>=0.7.28,<0.8` |
| langchain | `>=1.3,<1.4` | `>=1.4.6,<1.5` |
| langgraph | 1.2.4 | 1.2.16 |

## 2. 已落地

### 2.1 移除 callable backend（0.7 必需）

0.7 `FilesystemMiddleware` 拒绝 backend factory：`backend must be an initialized backend instance. Backend factories were removed`。

- 新增 `easy_agent/backends/context.py:ContextBackend`
  - 实现 `BackendProtocol` 同步/异步全部方法；
  - 运行期通过 LangGraph `get_runtime().context` 读取 `SessionContext`；
  - 按 `session_id/workspace/skills/memory/user` 缓存真实 `CompositeBackend`；
  - 用户不一致/缺 context fail-closed；
  - `resolve(ctx)` 供测试与显式调用。
- `agent.py:_make_backend_factory()` 返回 `ContextBackend` 实例，D2 共享图继续成立。
- 测试：`tests/agent/test_graph_cache.py` 改为断言实例并覆盖 context 解析/拒绝。

### 2.2 0.7 行为补丁

| 0.7 变化 | 处理 |
| --- | --- |
| `write_todos` 不再默认内置 | `agent._build_middleware()` 显式加入 `TodoListMiddleware()` |
| 新增 `delete` 工具 | `tools/registry.py` 登记 builtin，并加入 HITL（`approve/reject`，`hitl_order=5`） |
| `create_deep_agent` 签名/backend 实例 | 已适配；`test_framework_contract` 版本窗口更新 |
| `HarnessProfile` | 继续使用官方 profile（FW-04） |

### 2.3 StoreBackend 外置 spike（未切换默认）

- 新增 `tests/agent/test_store_backend_spike.py`：`StoreBackend(store=InMemoryStore(), namespace=...)` 完成 write/read/ls/edit/delete。
- 结论：`StoreBackend` 可作为 `/memories/` 目标；但当前 `memory_manager` 仍写本地 `memories/{user}/AGENTS.md`，**双源一致性未解决前不切换默认**。
- 目标接法（Phase 1 后续/Phase 3）：
  ```python
  CompositeBackend(
      default=StateBackend(),
      routes={
          "/workspace/": workspace_backend,   # S3/Sandbox 目标
          "/memories/": StoreBackend(namespace=lambda rt: (rt.context.safe_username, "memories")),
          "/user-skills/": StoreBackend(namespace=lambda rt: ("skills", rt.context.safe_username)),
      },
  )
  ```
  需要同步改造 `memory_manager` 读写路径与迁移脚本。

### 2.4 文件/Sandbox 抽象状态

| 项 | 状态 | 说明 |
| --- | --- | --- |
| Backend 实例化 | ✅ | `ContextBackend` 统一入口 |
| `/memories/` StoreBackend | spike 通过 | 默认仍 LocalShell，需双写迁移 |
| `/workspace/` 对象存储 | 未接入 | 当前 LocalShell + RWX；Phase 3 目标 |
| 外部 Sandbox | 未接入 | 保持 bwrap/Landlock；官方 Sandbox 需 provider 凭证/网络，Phase 3 评估 |
| 文件后端开关 | 预留 | `EASY_FILE_BACKEND`/`EASY_SANDBOX` 仅门禁占位 |

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest tests/`（0.7.28 + 1.4.6） | **1351 passed / 1 skipped / 0 failed（253s）** |
| StoreBackend spike | 1 passed |
| `test_framework_contract` | 版本窗口/profile/公开参数通过 |
| ruff | All checks passed |
| import graph | 0 SCC |

## 4. 风险 / 回滚

- `ContextBackend` 依赖 `get_runtime()`；脱离图执行（直接调 backend）必须 `resolve(ctx)` 或传 fallback context。
- `delete` 现在默认 HITL；前端若已有 approval UI 可复用，无 UI 的调用方需处理 `approval_required`。
- 0.7 第三方适配仍可能有未覆盖行为（长期记忆、子代理流）；升级窗口测试已固化。
- 回滚：恢复 pyproject/uv.lock 到 0.6.8 前版本 + revert `ContextBackend`/HITL 改动；不建议只回滚依赖。

## 5. 后续（Phase 2/3）

1. 记忆双写迁移到 StoreBackend + 历史数据迁移。
2. workspace 对象存储/外部 Sandbox；去掉 RWX 与 SYS_ADMIN。
3. stream_events/run 队列外置（Phase 2）。
4. K8s 迁移 Job/探针/PDB/HPA（Phase 3）。
