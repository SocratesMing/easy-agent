# 提示词目录化管理设计（2026-09-08）

## 目标

把散落各处的提示词（系统提示词、定时任务说明、会话记忆生成、记忆压缩、用户长期记忆生成）
统一到 `easy_agent/config/prompts/` 目录，启动时一次性加载，并同步优化文案。

## 现状问题

1. `system_prompt.md` 单文件 + `memory_manager.py` 里硬编码三个提示词常量，两套机制，改文案要动代码。
2. 定时任务说明在 `system_prompt.md` 与 `CreateScheduledTaskTool.description` 中重复维护。
3. `easy_agent/prompts/unit_test_prompt.md` 存在但代码无引用。
4. 优化前文案冗余：系统提示词缺运行环境/输出规范；记忆提示词规则重叠、缺可复用判据。

## 设计

### 目录结构

```
easy_agent/config/prompts/
├── system.md              # 主系统提示词
├── fragments/
│   └── scheduled_task.md  # 定时任务片段，拼接到系统提示词末尾
├── memory_update.md       # 会话记忆生成
├── memory_compress.md     # 记忆压缩
└── long_term_memory.md    # 用户长期记忆生成
```

放在 `config/` 下是为了与现有 config 目录整体挂载/覆盖（K8s ConfigMap）。

### 加载器 `easy_agent/services/prompt_loader.py`

- `get_prompts_dir()`：`EASY_PROMPTS_DIR` 环境变量 > `easy_agent/config/prompts`
- `load_system_prompt(config_dir, configured_path)`：
  1. 目录模式：`prompts/system.md` + 按文件名字典序拼接 `prompts/fragments/*.md`
  2. 兼容旧模式：`configured_path` 指向的单个 `.md`
  3. 兜底：代码内置 `DEFAULT_SYSTEM_PROMPT`
- `load_prompt(name)`：读取 `prompts/<name>.md`，缺失返回 `None`
- 缓存：以 `(路径, mtime)` 为键，避免每轮对话读盘
- 回退：任何读取异常都回落到内置默认，保证部署漏挂载不崩

### 占位符方案

用 `string.Template`（`$target_chars`）而非 `str.format`，因为提示词正文含 JSON 示例等
大括号内容，`format` 会抛 `KeyError`。`memory_manager.py` 中三处 `.format(...)` 改为
`Template(...).safe_substitute(...)`。

### 定时任务职责拆分

| 位置 | 内容 |
| --- | --- |
| `fragments/scheduled_task.md` | 触发规则：什么需求该创建、prompt 必须自包含、创建后确认下次时间 |
| 工具 `description` / `args_schema` | cron 字段定义与示例（调用工具时的权威上下文） |

## 兼容与风险

- `system_prompt_path` 配置保持向后兼容（传文件按文件读）。
- 内置默认提示词与文件内容保持一致，文件缺失时行为不降级。
- 占位符切换为 `$` 后，正文中若出现字面 `$` 需写成 `$$`（当前文案无此情况）。

## 测试

`tests/test_prompt_loader.py`：目录模式拼接、片段顺序、旧文件模式兼容、缺失回落默认值、
mtime 变化后重新加载、占位符可替换且 JSON 大括号不报错。
