# Increment 65 / Sandbox B：LangSmith Sandbox 远端执行报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 分支 | 工作区既有分支（Phase 0–3 之后；未 commit/push） |
| 范围 | 仅后端；不改前端/API/SSE/DB/schema/checkpoints/MCP；默认 local 不变 |
| 未执行 | commit / push / Batch 0/1 / Web Terminal / 全量测试（由 Lead 执行） |
| 结论 | 目标达成：`EASY_SANDBOX=langsmith` 时 execute 走官方 LangSmithSandbox，文件工具仍走本地；默认 local 路径不变 |

## 1. 交付内容

### 1.1 新增 `easy_agent/backends/langsmith_sandbox.py`

- `sandbox_mode()`：读取 `EASY_SANDBOX`，默认 `local`；只接受
  `local` / `langsmith`，其他值抛 `ValueError`（fail-closed）。
- `create_langsmith_backend(...)`：
  - `SandboxClient()` + `create_sandbox(snapshot_name=..., wait_for_ready=True)`
    + `LangSmithSandbox(...)`；
  - 缺 `LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY`、SDK 不可用、创建失败均抛
    带上下文的 `RuntimeError`；
  - `sandbox_factory` 测试注入点：零参调用，返回 fake/真实 backend；注入时
    不检查凭据、不访问网络；
  - 支持 `EASY_SANDBOX_REMOTE_ROOT`（默认 `/workspace`）与
    `EASY_SANDBOX_SYNC_MAX_MB`（默认 20）。
- `LangSmithExecuteShell(_PathTranslatingShell)`：
  - 构造传 `sandbox_enabled=False`，文件读写/路径翻译继承本地语义；
  - `execute`：上传本地 workspace → `cd {remote_root} && {command}` →
    远端执行 → 回写远端文件；
  - 上传排除 `.deps` / `.venv` / `node_modules` / `__pycache__` 与符号链接，
    超限单文件跳过并 warning；
  - 首次上传失败返回 `exit_code=1` 并拒绝执行；后续上传失败 warning 后继续；
  - 远端 execute 异常向上抛出；回写失败只 warning；
  - 命令中的 `/workspace` 虚拟前缀与本地 workspace 绝对路径替换为
    `remote_root`；输出中 `remote_root` 反向替换回虚拟前缀；
  - `aexecute` 用 `asyncio.to_thread` 包装。
- 懒加载 `LangSmithSandbox` / `langsmith.sandbox`：默认 local 模式 import
  不会因缺少 sandbox 依赖失败。

### 1.2 `easy_agent/agent.py`

- `_build_backend_for_context()`：
  - `sandbox_mode() == "langsmith"` → default 为
    `create_langsmith_backend(...)`；
  - 否则保持原 `_PathTranslatingShell(...)`；
  - `/workspace/`、`/memories/`、`/user-skills/`、external 路由与
    `path_mappings` 不变。
- `_make_backend_factory()`：给 `ContextBackend` 传入静态 route 前缀，供
  FilesystemMiddleware 启动期权限检查；真实路由仍按 context 构建。
- 未新增 sandbox 缓存层：沿有 `ContextBackend` 的 per-context cache
  （默认 16），满足“不要每个工具调用新建”。

### 1.3 `easy_agent/backends/context.py`（前置修复）

Phase 1 的 `ContextBackend` 只代理文件操作，未声明
`SandboxBackendProtocol`，因此 deepagents 0.7 的 `supports_execution()` 判定
共享图不支持 execute，execute 工具在 D2 路径下实际不可用。

本批最小修复：

- 继承 `SandboxBackendProtocol`，代理 `execute` / `aexecute` / `id` 到
  当前 context 的 `CompositeBackend`；
- `CompositeBackend.register(ContextBackend)` 注册为虚子类；
- 提供静态 `default` / `routes` / `sorted_routes` / `artifacts_root` 占位视图：
  - `default` 是 `SandboxBackendProtocol` 占位对象（`supports_execution` 为真）；
  - `routes` 是启动期 route 前缀（`_all_paths_scoped_to_routes` 通过）；
  - `sorted_routes` 为空（`_route_host_path_prompt()` 保持为空，不改提示词）；
  - `artifacts_root = "/"`（与原先非 CompositeBackend 行为一致）。
- 静态视图不参与实际文件/命令路由，真实行为仍由 per-context
  `CompositeBackend` 决定；local 模式的权限、提示词、state schema 保持不变。

### 1.4 测试

新增 `tests/agent/test_langsmith_sandbox.py`（17 个用例，全部 fake backend，
不访问网络）：

- `sandbox_mode()` 默认 local、大小写/空白归一、非法值报错；
- 工厂缺 key 的清晰错误、`sandbox_factory` 创建失败包装、注入 fake 不需要凭据；
- execute：上传/远端调用/下载回写、命令包装 `cd /workspace && ...`、
  自定义 remote root、虚拟前缀映射、timeout 传递、错误传播；
- 首次上传失败拒绝执行、后续上传失败继续、回写不删除本地额外文件；
- 排除目录与大文件上限；
- `aexecute` 正常；
- local 模式 `_build_backend_for_context` 仍返回原 `PathTranslatingShell`；
- langsmith 模式使用注入的远端 default 且本地 routes 不变；
- `ContextBackend` 是 `SandboxBackendProtocol` 且 execute/aexecute 代理。

## 2. 验证

WSL、仓库 `.venv`，按任务指定的子集（未跑全量）：

```
pytest tests/agent/test_langsmith_sandbox.py tests/agent/test_graph_cache.py \
       tests/agent/test_hitl.py tests/api/test_chat.py -q
# 61 passed
```

```
uvx ruff check .
# All checks passed!
```

```
python scripts/quality/import_graph.py --check-scc
# import graph: 226 modules, 525 internal edges, 0 SCC(s) / 0 modules
# [check-scc] OK — 实际 SCC 与白名单完全一致
```

说明：任务给出的基线是 1399 passed；本批按约束未复跑全量，也未跑
Batch 0/1 与 Web Terminal。全量/集成验证由 Lead 执行。

## 3. 设计取舍

1. **opt-in 而非默认**：`EASY_SANDBOX` 默认 local，只有显式 langsmith 才创建
   远端实例；local 代码路径（bwrap/Landlock）完全保留。
2. **execute-only hybrid**：文件工具继续本地，避免把 `/memories/`、
   `/user-skills/`、external 路由复制到远端；代价是远端命令看不到这些路径。
3. **每次 execute 全量同步**：实现简单、语义直观；没有增量/删除同步。
4. **首次同步 fail-closed**：避免命令在空 workspace 上执行并产生误导性结果；
   后续同步失败不阻断已能工作的远端状态。
5. **ContextBackend 静态占位**：不重构 D2 的 per-context 路由，只满足框架
   启动期“execute + permissions”门禁；风险是占位对象与真实路由不同步，但
   它们不参与实际执行，且 `sorted_routes` 刻意保持为空以不改变提示词。
6. **不实现 destroy**：依赖 LangSmith idle TTL，避免在工具调用路径上引入
   额外生命周期管理；代价见风险。

## 4. 未做 / 风险（诚实清单）

| 项 | 说明 |
| --- | --- |
| **真实 LangSmith 集成测试** | 本批全部用 fake backend；没有真实网络/凭据验证，`create_sandbox` 的返回字段/错误码仅按官方签名对接。 |
| **sandbox destroy / 生命周期** | 未实现；依赖 LangSmith idle TTL。进程退出、缓存驱逐不会主动销毁远端实例。 |
| **持久化 / snapshot** | 未实现；远端改动不提交 snapshot，sandbox 过期即丢失。 |
| **远端删除同步** | 未实现。回写只覆盖/新增，不删除本地；本地删除的文件可能被远端旧副本回写复活。 |
| **permissions / `/memories/` / `/user-skills/`** | 未同步到远端；远端命令不能访问这些虚拟路径，需要先复制到 workspace。 |
| **依赖目录** | `.deps` / `.venv` / `node_modules` / `__pycache__` 不同步，远端命令可能缺少 Python/Node 依赖。 |
| **符号链接 / 权限位 / 时间戳** | 不同步；只传文件字节，可执行位会丢。 |
| **大文件** | 超过 `EASY_SANDBOX_SYNC_MAX_MB`（默认 20）的文件静默跳过（仅 warning）；命令可能看不到。 |
| **命令路径替换** | 启发式，只替换 workspace 虚拟前缀/本地绝对路径/remote_root；不替换 `/user-skills/...` 等其他 route。 |
| **输出回写成本** | 每次 execute 下载远端 root 下所有文件；大产物会增加延迟/带宽，当前无增量或忽略规则。 |
| **glob 截断 / 文件数量** | `glob("**/*")` 截断时只回写已列出文件；没有总文件数/总大小预算，超大 workspace 延迟高。 |
| **数据出境/密钥** | workspace 全量上传（仅排除四类目录），`.env`/密钥不会被自动排除；LangSmith 凭据与沙箱费用由部署方承担。 |
| **多副本严格门禁** | `runtime_features` 仍将 sandbox 标记为未外置；严格模式多副本 prod 会拒绝 `EASY_SANDBOX=langsmith`，本批有意不改。 |
| **ContextBackend 虚子类注册** | 依赖 `CompositeBackend.register()` 与占位 `default/routes`；若未来 deepagents 改变 `supports_execution` / 权限门禁实现，需要同步调整。 |
| **全量测试** | 未跑；仅指定子集 61 passed。Lead 需跑全量 1399+ 与真实 sandbox 冒烟。 |

## 5. 后续建议

1. 用真实 LangSmith 账号做一次 `EASY_SANDBOX=langsmith` 冒烟（创建、执行、
   回写、超时、TTL 到期）。
2. 增加 sandbox 生命周期钩子（应用关闭/缓存驱逐时 destroy 或记录 id）。
3. 增加同步排除清单（`.env*`、`*.pem`、大目录）与增量/删除同步。
4. 为远端命令依赖提供可选初始化脚本（在 sandbox 内 `pip install`/`npm ci`）。
5. 评估把 `runtime_features.sandbox` 纳入外置能力（需先完成生命周期与
   多副本一致性）。