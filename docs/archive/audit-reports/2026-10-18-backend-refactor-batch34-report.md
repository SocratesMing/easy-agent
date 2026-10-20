# 后端架构重构批次 3/4：agent 装配与 API 依赖收口

## 批次 3：agent.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `agent.py` | 1264 → 753 | `EasyAgent` 核心（初始化/建图/HITL/缓存） |
| `agent_backend.py` | 新增 145 | `_build_backend_for_context`（Composite/Local/Sandbox 路由装配） |
| `agent_workspace.py` | 新增 365 | `AgentWorkspaceMixin`：memory/skills/permissions/workspace/external dirs 方法 |
| `agent_middleware.py` | 新增 66 | `build_agent_middleware`（TodoList→Logging→CallLimit 顺序） |

- 纯移动：`_build_backend_for_context` 与 14 个 mixin 方法逐字节一致；
  middleware 仅 `self.*`→参数 + 缩进变换，AST body 等价。
- `EasyAgent(AgentWorkspaceMixin)`；旧导入 `_build_backend_for_context`/
  `LONG_TERM_MEMORY_VIRTUAL_PATH`/`_PathTranslatingShell` 兼容。
- 测试 patch 点同步到新实现模块（sandbox 测试），意图不变。

## 批次 4：api/deps.py

- 新增 `easy_agent/api/deps.py`：5 个 `get_*_repository` + 5 个 `as_*`；
  `get_user_repository` 仍来自 `middleware.auth` 并在此 re-export。
- `chat/chat_live/files/expert/scheduled_tasks/sessions/auth` 删除本地 provider，
  改从 deps 导入；旧模块名 re-export，`app.dependency_overrides` 旧路径仍生效。
- `chat_live.py` 不再复制 chat provider，消除重复。
- API 文件不再出现 `Depends(get_database)`；唯一入口 `deps._resolve_database`。
- C1 门禁同步：DOMAINS 增加 chat_live/sessions；API 文件 `Depends(get_database)==0`；
  provider 身份断言改为 `is deps.*`；路由数量/路径/OpenAPI 总数保持。

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1607 passed / 1 skipped / 0 failed（290.2s）** |
| agent/deps 定向 | 71 passed |
| import 图 | 0 SCC（239 modules / 567 edges） |
| double-write | OK（153 methods / 115 delegations） |
| ruff | All checks passed |
| API/SSE/存储格式 | 未改 |

## 风险

1. 私有 monkeypatch 目标迁移（agent backend/middleware、expert helpers 等），
   仓库内已适配，仓库外脚本需跟进。
2. `deps._resolve_database` 为唯一数据库依赖节点，依赖链多一层；override 语义已测。
3. `agent.py` 仍有 753 行；`_create_agent`/graph cache/HITL 主体尚未拆。
4. 余下最长文件：`knowledges/service.py` 2402、`ragflow/client.py` 1470、
   `api/chat.py` 814、`memory_manager.py` 582、`skill_center.py` 782。
