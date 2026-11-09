# Sandbox B：可选 LangSmith Sandbox 远端执行后端（实验性）

| 项 | 内容 |
| --- | --- |
| 状态 | 已实现（仅后端；未 commit/push） |
| 日期 | 2026-10-18 |
| 范围 | opt-in `EASY_SANDBOX=langsmith`；默认 `local` 行为不变 |
| 新增 | `easy_agent/backends/langsmith_sandbox.py` |
| 修改 | `easy_agent/agent.py`（backend 选择）、`easy_agent/backends/context.py`（execute 可达性前置修复） |
| 测试 | `tests/agent/test_langsmith_sandbox.py`（fake sandbox，不联网） |
| 回归 | `tests/agent/test_langsmith_sandbox.py tests/agent/test_graph_cache.py tests/agent/test_hitl.py tests/api/test_chat.py -q` → 61 passed |
| 报告 | `docs/audit/2026-10-18-increment65-sandbox-report.md` |

## 1. 目标与边界

本批只做**可选的远端 execute 后端**，不改前端、API/SSE 契约、DB/schema、
checkpoint、MCP、streaming。默认仍使用现有 bwrap/Landlock 本地 shell；只有显式
设置 `EASY_SANDBOX=langsmith` 时才启用 LangSmith Sandbox。

采用 **hybrid** 语义：

- `read_file` / `write_file` / `edit_file` / `delete` / `ls` / `glob` / `grep`
  等文件工具继续使用本地 workspace（`CompositeBackend` 的本地 routes 不变）；
- 只有 `execute` 被转发到远端 LangSmith Sandbox；
- 每次 execute 前后做 best-effort 工作区同步：本地 → 远端（执行前），
  远端 → 本地（执行后回写）。

不实现远端 sandbox 的 destroy/持久化：生命周期依赖 LangSmith 自身的 idle TTL；
见 §8 风险。

## 2. 上游能力

使用官方接口（deepagents 0.7.28）：

```python
from langsmith.sandbox import SandboxClient
from deepagents.backends import LangSmithSandbox

client = SandboxClient()  # 读取 LANGSMITH_API_KEY / LANGSMITH_ENDPOINT
sandbox = client.create_sandbox(
    snapshot_name=os.getenv("EASY_LANGSMITH_SNAPSHOT") or None,
    wait_for_ready=True,
)
backend = LangSmithSandbox(sandbox)
backend.execute("cd /workspace && ...", timeout=...)
```

`LangSmithSandbox` 实现 `SandboxBackendProtocol`，提供 `execute` /
`upload_files` / `download_files` / `glob` / `aexecute`。本仓库不复制其内部实现，
只做 hybrid 包装。

## 3. 配置与工厂

### 3.1 开关

```python
sandbox_mode() -> "local" | "langsmith"
```

- 未设置、空串、`local` → `local`（默认）；
- `langsmith` → `langsmith`；
- 其他值 → `ValueError`（fail-closed，避免拼写错误静默退回本地）。
  旧文档里的 `external` 只是 Phase 1 占位，本批未实现；设置它会 fail-closed。

### 3.2 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `EASY_SANDBOX` | `local` | `local` / `langsmith` |
| `LANGSMITH_API_KEY` | 无 | 必需；也接受 `LANGCHAIN_API_KEY` |
| `LANGSMITH_ENDPOINT` | LangSmith 默认 | 指向 sandbox API 所在环境 |
| `EASY_LANGSMITH_SNAPSHOT` | 无 | 传给 `create_sandbox(snapshot_name=...)` |
| `EASY_SANDBOX_REMOTE_ROOT` | `/workspace` | 远端 workspace 根目录（绝对路径） |
| `EASY_SANDBOX_SYNC_MAX_MB` | `20` | 单文件同步上限（MB），超限跳过并 warning |

### 3.3 `create_langsmith_backend(...)`

- 先读 `EASY_SANDBOX_REMOTE_ROOT`、`EASY_SANDBOX_SYNC_MAX_MB`；
- 未注入 `sandbox_factory` 时：
  - 检查 `LANGSMITH_API_KEY` / `LANGCHAIN_API_KEY`，缺失抛清晰 `RuntimeError`；
  - 懒加载 `deepagents.backends.LangSmithSandbox` 与 `langsmith.sandbox`，
    import 失败只在 langsmith 模式下抛错，默认 local 模式不受影响；
  - `SandboxClient().create_sandbox(snapshot_name=..., wait_for_ready=True)`，
    创建/包装失败统一抛 `RuntimeError`（带 snapshot 名称）；
- `sandbox_factory` 是测试注入点：零参可调用对象，直接返回 fake 或真实
  `LangSmithSandbox` backend；注入时跳过 LangSmith 凭据检查；
- 返回 `LangSmithExecuteShell`。

## 4. `LangSmithExecuteShell`

继承 `easy_agent/backends/shell.py:_PathTranslatingShell`，构造时
`sandbox_enabled=False`，因此：

- 文件读写/路径翻译沿用父类（本地 workspace）；
- 不会启用 bwrap/Landlock（命令不在本地跑）；
- 默认 `cwd` 仍是本地 workspace，便于文件工具。

### 4.1 execute 流程

1. `_sync_to_remote()`：
   - 遍历本地 workspace，跳过 `.deps` / `.venv` / `node_modules` /
     `__pycache__`（任意层级）与符号链接；
   - 跳过超过 `EASY_SANDBOX_SYNC_MAX_MB` 的单文件（warning，不阻断）；
   - 首次/新增目录先 `mkdir -p`；
   - 通过注入的 `upload_files` 上传到远端 `{remote_root}/{相对路径}`。
2. 命令包装：`cd {remote_root} && {command}`；
   - 命令中的 workspace 虚拟前缀（默认 `/workspace`）与本地 workspace
     绝对路径会替换为 `remote_root`；
   - `remote_root` 默认就是 `/workspace`，因此模型写的 `/workspace/...`
     在远端可直接使用。
3. 远端执行：`sandbox_backend.execute(wrapped, timeout=timeout)`；
   - `timeout=None` 原样传递（使用 LangSmith backend 默认值）；
   - 远端异常向上抛出（不吞）。
4. `_sync_from_remote()`：
   - `glob("**/*", path=remote_root)` 列出远端文件；
   - 分批 `download_files`，覆盖同名本地文件；
   - **不删除**远端不存在的本地额外文件；
   - 越界路径（`..`/symlink 逃逸）拒绝写回；
   - 该阶段任何失败只记 warning，不影响 execute 返回值。

### 4.2 失败语义

| 阶段 | 首次 | 后续 |
| --- | --- | --- |
| 上传/目录创建失败 | 返回 `ExecuteResponse(exit_code=1)`，**拒绝执行** | warning 后继续（使用上次同步状态） |
| 远端 execute 异常 | 向上抛出 | 同上 |
| 回写/下载失败 | warning，返回远端执行结果 | 同上 |

“首次”以是否成功完成过一次上传为准；这样既避免命令看不到输入文件，又不会
因为执行后产生的临时文件无法读取而丢掉命令结果。

### 4.3 异步

`aexecute` 用 `asyncio.to_thread(self.execute, ...)`，包含前后同步，避免在事件
循环里做阻塞网络 IO。

## 5. agent 接入

`easy_agent/agent.py:_build_backend_for_context()`：

- `sandbox_mode() == "langsmith"` 时 default 使用
  `create_langsmith_backend(...)`；
- 否则保持原 `_PathTranslatingShell(...)`，本地模式代码路径不变；
- `/workspace/`、`/memories/`、`/user-skills/`、external 路由与文件权限
  仍指向本地 `LocalShellBackend`；
- sandbox 实例由现有 `ContextBackend` 按 context 缓存（cache_size 默认 16），
  不会每次工具调用新建。

## 6. ContextBackend 前置修复（必要）

Phase 1 的 `ContextBackend` 只代理文件操作，没有 `execute`，也没有声明
`SandboxBackendProtocol`。deepagents 0.7 的 `supports_execution()` 会因此判定
共享图 backend 不支持命令执行，execute 工具在 D2 共享图路径下不可用。

本批做了最小修复：

- `ContextBackend` 继承 `SandboxBackendProtocol`，代理
  `execute` / `aexecute` / `id` 到当前 context 的 `CompositeBackend`；
- 通过 `CompositeBackend.register(ContextBackend)` 注册为虚子类，并提供
  `default` / `routes` / `sorted_routes` 的**静态占位视图**：
  - `default` 返回占位 `SandboxBackendProtocol`（让 `supports_execution` 为真）；
  - `routes` 返回启动期静态 route 前缀（让 `_all_paths_scoped_to_routes` 通过）；
  - `sorted_routes` 返回空列表（保持 `_route_host_path_prompt()` 为空，
    不改变现有系统提示词）；
  - `artifacts_root = "/"`（与原先非 CompositeBackend 行为一致）。
- 真实文件操作/命令仍由 `ContextBackend` 按 context 代理到运行期
  `CompositeBackend`；静态视图不参与实际路由。

这样 local 模式的权限、路径提示、state schema 语义保持不变，同时 execute
（本地或 langsmith）重新可达。`tests/api/test_chat.py` 的 agent 创建用例覆盖了
“带 permissions 的 execute backend”路径。

## 7. 同步与路径限制（重要）

1. **只同步 workspace**：`/memories/`、`/user-skills/`、external 路由没有
   同步到远端。远端命令不能读取这些虚拟路径；需要时先把文件复制到
   `/workspace/...`。
2. **远端删除不回流**：回写只覆盖/新增，不删除本地文件；远端删除的文件在
   本地仍存在。反之，本地删除的文件可能被远端旧副本回写“复活”。
3. **不保留权限位/时间戳/符号链接**：只传文件字节；可执行位不会同步。
4. **依赖目录不同步**：`.deps` / `.venv` / `node_modules` / `__pycache__`
   被排除，远端命令若依赖这些目录会失败；需在远端安装依赖或使用纯脚本。
5. **超限文件静默跳过**：超过 `EASY_SANDBOX_SYNC_MAX_MB` 的文件不上传，
   命令可能看不到它；日志有 warning。
6. **命令路径替换是启发式**：只替换 workspace 虚拟前缀、本地 workspace
   绝对路径和 remote_root；`/user-skills/...` 等不会替换。
7. **输出回写是 best-effort**：远端命令可能生成大量/超大文件，全部下载会
   增加延迟与带宽；当前没有增量同步或忽略规则（除单文件上传上限）。
8. **glob 截断/超时**：`glob("**/*")` 若被截断，只回写已列出的文件；
   未列出的远端产物不会出现在本地。
9. **无文件数/总大小上限**：每次 execute 仍会遍历并上传/下载全部（受单文件
   上限约束），超大 workspace 会增加延迟；后续可加总大小/文件数预算。

## 8. 安全、费用与风险

- **实验性**：LangSmith Sandbox 仍是 alpha 能力，接口可能变化；本后端不做
  生产 SLA 承诺。
- **数据出境**：workspace 内容会上传到 LangSmith Sandbox。workspace 中的
  `.env`、密钥、用户数据默认不会被排除；不要把敏感文件放进会话 workspace，
  或等待后续增加排除清单。
- **凭据**：需要 LangSmith API key；key 只从环境变量读取，不写入配置/日志。
- **费用**：远端 sandbox 创建、执行、存储、快照均可能计费；idle TTL 到期前
  实例会占用资源。
- **未做 destroy**：不主动销毁 sandbox；依赖 LangSmith idle TTL。进程重启后
  旧 sandbox 可能仍存活到 TTL，费用与残留风险由平台侧兜底。
- **未做持久化/快照**：远端改动只在 sandbox 生命周期内有效；不提交 snapshot，
  也不保证跨 execute 之外的状态。
- **无远端删除同步**：见 §7.2。
- **多副本严格门禁**：`easy_agent/runtime_features.py` 仍把 `sandbox` 视为
  未实现外置；`EASY_MULTI_REPLICA_STRICT=1` + 多副本 prod 会拒绝
  `EASY_SANDBOX=langsmith`。本批有意不改该门禁。
- **未跑全量测试**：按任务要求只跑指定子集；全量由 Lead 执行。

## 9. 验证

```bash
pytest tests/agent/test_langsmith_sandbox.py \
       tests/agent/test_graph_cache.py \
       tests/agent/test_hitl.py \
       tests/api/test_chat.py -q
# 61 passed

uvx ruff check .
# All checks passed!

python scripts/quality/import_graph.py --check-scc
# import graph: 226 modules, 525 internal edges, 0 SCC(s)
# [check-scc] OK
```