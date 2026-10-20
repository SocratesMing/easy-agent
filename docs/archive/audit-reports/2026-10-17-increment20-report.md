# Increment 20 执行报告（ARCH-09：环境变量读取统一）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 19） |
| 依据 | 审计 ARCH-09（25+ 处手写 os.getenv、语义不一致） |
| 约束 | 行为保持一致；不改变任何配置默认值 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增环境变量 helper | `easy_agent/env.py`（顶层模块，避免 utils 包 `__init__` 触发 config 循环导入）：`env_str`（可选 strip）/`env_bool`（1/true/yes/on）/`env_int`（失败回退）/`env_list`（逗号分隔去空） | 统一 30+ 处散落的解析语义 |
| 替换运行期读取 | 替换 16 个模块的 20+ 处：app（LLM probe/checkpoint/agent_env）、client_ip、streaming、rate_limit、agent_manager、mcp、environment、prompt_loader、bootstrap、config、knowledge_impl、landlock、deployment、scheduler、public_expert_service | 消除“某些地方 `in (1,true,yes)`、某些直接比较”的漂移；布尔解析有单测保护 |
| 测试 | 新增 `tests/utils/test_env.py` 3 项（str/bool/int/list 边界） | 相关 18 项测试通过 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 18 passed（env/deployment/runtime/multi-worker/scheduler） |
| 全量 `pytest tests/` | **961 passed, 1 skipped, 0 failed, 0 error（240.4s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | 所有默认值与之前一致；`EASY_LLM_PROBE` 等开关语义不变 |

## 2.1 实施中修正

- `env.py` 最初放在 `easy_agent/utils/` 下，config.py 顶部 import 会触发 `utils/__init__` → task_logger → config 的循环导入；已移到顶层 `easy_agent/env.py`。
- `landlock.py` 会被 sandbox 以脚本方式执行（`__main__`，无 package），因此保留原生 `os.environ` 读取，不引入相对 import（否则子进程 `ImportError`）。

## 3. 未完成/后续

1. 仍有约 10 处 `os.environ.get` 属于“配置展开/密钥读取”（如 config `${VAR}` 展开、JWT secret、knowledge 配置），保留原生读取以避免改变语义。
2. ARCH-16 版本化迁移、ARCH-08 API 契约、FW-06/09/10、多副本 Phase B、ARCH-03 Phase 3 未开始。
3. Batch 0/1 安全项（4 P0 + 8 P1）仍未执行。
