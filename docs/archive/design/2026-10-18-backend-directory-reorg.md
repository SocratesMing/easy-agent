# 后端目录重构（2026-10-18）

## 目标

不改变业务逻辑/API/SSE/存储格式，把 `easy_agent/` 根目录分散模块按职责归入
子包；统一绝对导入，更新全仓库引用与入口/配置路径；完成编译、启动冒烟、
全量测试。

## 新目录结构

```
easy_agent/
├── __init__.py                 # 包级导出（EasyAgent/Config/create_model/discover_skills）
├── app.py                      # FastAPI 应用入口（保留）
├── knowledge_impl.py           # 知识域宿主桥接（保留）
├── agent/                      # Agent 运行时（职责：组装、记忆、技能、HITL、模型）
│   ├── __init__.py             # 兼容门面：re-export 旧 easy_agent.agent 公共名
│   ├── core.py                 # EasyAgent + 建图（原 agent.py）
│   ├── backend.py              # CompositeBackend 装配（原 agent_backend.py）
│   ├── graph_cache.py          # D2 编译图缓存（原 agent_graph_cache.py）
│   ├── middleware.py           # middleware 栈（原 agent_middleware.py）
│   ├── workspace.py            # memory/skills/permissions/workspace mixin
│   ├── hitl.py                 # HITL 规则
│   ├── skills.py               # 技能发现/校验
│   ├── subagents.py            # 子代理定义
│   ├── summarization.py        # 摘要工厂适配
│   └── model.py                # LLM factory（原 model.py）
├── bootstrap/                  # 启动/配置/环境/日志/入口
│   ├── config.py               # Config 门面
│   ├── config_models.py        # Pydantic 配置模型
│   ├── config_validation.py    # 键校验/${VAR} 展开
│   ├── env.py                  # env_str/env_bool/...
│   ├── runtime_config.py       # 运行期配置持有者
│   ├── runtime_features.py     # 多副本特性开关/门禁
│   ├── logger.py               # AgentLogger
│   ├── web_runner.py           # easy-web 入口（原根 web_runner.py）
│   └── initialization/         # 环境/日志/初始化（原 initialization/）
├── persistence/                # 持久化适配
│   ├── checkpoints.py          # LangGraph checkpoint
│   └── stores.py               # LangGraph store
├── sandboxing/                 # 代码执行隔离
│   ├── sandbox.py              # bwrap/Landlock
│   ├── landlock.py
│   └── fs_safety.py            # 外部目录安全
├── security/
│   └── prompt_guard.py         # 提示词安全组装
├── backends/                   # DeepAgents backend 适配（shell/context/langsmith）
├── api/                        # HTTP 路由（含 deps.py）
├── services/                   # 用例/编排/流式/任务/记忆等
├── db/                         # 连接 + schema + repositories
├── knowledges/                 # 知识库域
├── tools/                      # Agent 工具
├── middleware/                 # JWT/限流/指标
├── models/                     # API/DB Pydantic 模型
├── utils/                      # 通用工具
└── config/                     # YAML/prompts 资源（非 Python 包）
```

## 路径调整摘要

| 旧路径 | 新路径 |
| --- | --- |
| `easy_agent/agent.py` | `easy_agent/agent/core.py` |
| `easy_agent/agent_backend.py` | `easy_agent/agent/backend.py` |
| `easy_agent/agent_graph_cache.py` | `easy_agent/agent/graph_cache.py` |
| `easy_agent/agent_middleware.py` | `easy_agent/agent/middleware.py` |
| `easy_agent/agent_workspace.py` | `easy_agent/agent/workspace.py` |
| `easy_agent/hitl.py` | `easy_agent/agent/hitl.py` |
| `easy_agent/skills.py` | `easy_agent/agent/skills.py` |
| `easy_agent/subagents.py` | `easy_agent/agent/subagents.py` |
| `easy_agent/summarization.py` | `easy_agent/agent/summarization.py` |
| `easy_agent/model.py` | `easy_agent/agent/model.py` |
| `easy_agent/config.py` | `easy_agent/bootstrap/config.py` |
| `easy_agent/config_models.py` | `easy_agent/bootstrap/config_models.py` |
| `easy_agent/config_validation.py` | `easy_agent/bootstrap/config_validation.py` |
| `easy_agent/env.py` | `easy_agent/bootstrap/env.py` |
| `easy_agent/runtime_config.py` | `easy_agent/bootstrap/runtime_config.py` |
| `easy_agent/runtime_features.py` | `easy_agent/bootstrap/runtime_features.py` |
| `easy_agent/logger.py` | `easy_agent/bootstrap/logger.py` |
| `easy_agent/web_runner.py` | `easy_agent/bootstrap/web_runner.py` |
| `easy_agent/initialization/` | `easy_agent/bootstrap/initialization/` |
| `easy_agent/checkpoints.py` | `easy_agent/persistence/checkpoints.py` |
| `easy_agent/stores.py` | `easy_agent/persistence/stores.py` |
| `easy_agent/sandbox.py` | `easy_agent/sandboxing/sandbox.py` |
| `easy_agent/landlock.py` | `easy_agent/sandboxing/landlock.py` |
| `easy_agent/fs_safety.py` | `easy_agent/sandboxing/fs_safety.py` |
| `easy_agent/prompt_guard.py` | `easy_agent/security/prompt_guard.py` |

## 入口/配置同步

- `pyproject.toml` console script：`easy-web = easy_agent.bootstrap.web_runner:run_web`。
- `main.py` 改从 `easy_agent.bootstrap.web_runner` 导入。
- `bootstrap/config.py` 的 `get_package_dir()` 指向 `easy_agent/`（YAML/prompts 仍在
  `easy_agent/config/`）；`bootstrap/web_runner.py` 的项目根按 `parents[2]` 解析。
- 所有 Python 相对导入改为绝对模块路径，再按新旧路径映射批量更新；测试同一批更新。

## 验证

| 检查 | 结果 |
| --- | --- |
| 包编译 | `compileall` 通过 |
| 导入 | `import easy_agent`, `easy_agent.app` 通过 |
| 启动 | `uvicorn easy_agent.app:app` → `Application startup complete` |
| 配置加载 | `check_config_schema.py --check` 通过 |
| 全量 pytest | **1607 passed / 1 skipped / 0 failed** |
| import SCC | 0 SCC |
| ruff | All checks passed |

## 兼容边界

- `easy_agent.agent` 保留包门面，旧 `from easy_agent.agent import EasyAgent/
  _build_backend_for_context/...` 可用；但 `easy_agent.agent.create_deep_agent`
  等模块级 patch 目标已迁到 `easy_agent.agent.core`，测试已同步。
- 旧根模块路径不再存在；外部脚本若直接 import `easy_agent.config`/
  `easy_agent.checkpoints` 等需改用新路径（仓库内已全部更新）。
