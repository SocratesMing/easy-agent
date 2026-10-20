# Increment 58 · 专项后续增强（记忆锁 + 引用规则）报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 结论 | 完成两条后端可独立落地项：记忆 JSONL/Markdown 跨进程文件锁与原子双写；外部连接器/知识库引用规则与来源标注；相关测试、ruff、SCC 门全部通过 |
| 范围 | 后端；未改前端、API、streaming、checkpoints、MCP、DB、`agent.py`、`conftest`；未 commit；未执行 Batch 0/1 |
| 依据 | `docs/design/2026-10-18-structured-memory.md` §8 并发遗留；`docs/design/2026-10-18-prompt-security.md` R2；本任务设计文档 `docs/design/2026-10-18-specialty-hardening.md` |

## 1. 结论

- `memory_store` 新增标准库跨进程文件锁：POSIX `fcntl.flock`，锁文件由 JSONL
  sidecar 推导（`.memory.jsonl.lock`），Markdown 与 sidecar 共用同一把锁；
  进程内可重入、进程间互斥。
- Windows / 无 `fcntl` / `flock` 失败的平台降级为进程内串行并 **warning 一次**，
  不阻断记忆读写；`cross_process_lock_supported()` 可探测真实状态。
- Markdown 写入从非原子 `write_text` 改为 `atomic_write_text`（临时文件 +
  `os.replace` + `fsync`）；`write_memory` 与两个 `update_*_after_session` 的
  “sidecar + Markdown”双写进入同一临界区，读路径用共享锁。
- 新增提示词片段 `30-external-data-citation.md`：连接器/知识库结果只作为数据，
  回答数据类问题必须标注来源，并说明为空 / 未验证；同步
  `prompt_loader.DEFAULT_SYSTEM_PROMPT`，保持内置兜底与文件逐字一致。
- 目标测试 **59 passed**；`uvx ruff check .` **All checks passed**；
  `python3 scripts/quality/import_graph.py --check-scc` **0 SCC / 217 modules / 488 edges**（模块计数随并行 wave 改动变化，门禁结论为 0 SCC）。

## 2. 交付物

| 文件 | 变更 |
| --- | --- |
| `easy_agent/services/memory_store.py` | 新增 `memory_lock` / `memory_lock_path` / `atomic_write_text` / `cross_process_lock_supported`；`MemoryStore.load/save`、`write_structured_memory`、`sync_from_markdown` 进锁；逻辑拆为 `_*_locked` 私有实现保持可重入 |
| `easy_agent/services/memory_manager.py` | `read_memory` 共享锁、`_write_markdown_file` 原子写、`write_memory` 与两个 update 的双写临界区 |
| `easy_agent/config/prompts/fragments/30-external-data-citation.md` | 新增 E1–E5 引用规则 |
| `easy_agent/services/prompt_loader.py` | `DEFAULT_SYSTEM_PROMPT` 追加同一片段文本 |
| `tests/services/test_memory_store.py` | 新增 6 项：锁路径、可重入、降级 warning（并验证降级后仍可读写）、跨进程 RMW 串行化、并发双写一致性、开关回退 |
| `tests/services/test_system_prompt.py` | 新增引用规则与禁用词契约断言 |
| `tests/test_prompt_security.py` | 新增连接器/知识库来源标注测试 |
| `docs/design/2026-10-18-specialty-hardening.md` | 设计文档 |
| 本文件 | 审计报告 |

## 3. 锁实现与跨平台行为

- **锁文件**：`store_path_for(target)` → `.<sidecar>.lock`。`memory.md` 与
  `memory.jsonl` 得到同一路径，因此双写可共用一个临界区。
- **进程内**：`threading.RLock` 注册表 + `threading.local()` 深度；嵌套
  `memory_lock` 只增深度，不重复 flock。覆盖了
  `write_structured_memory → MemoryStore.save`、`write_memory →
  sync_from_markdown → save` 等嵌套路径。
- **进程间**：最外层 `flock(LOCK_EX)`；读用 `LOCK_SH`。锁随 fd 关闭 / 进程退出
  释放；锁文件不删除，避免 unlink 后锁到新 inode 的互斥漏洞。
- **降级**：`_fcntl is None`（Windows）或 `flock` 抛 `OSError/AttributeError`
  或锁文件无法创建时，只保留进程内互斥并 warning 一次；不会让记忆写入失败。
- **原子写**：JSONL 与 Markdown 都走 `_atomic_write_text`；读方看不到半截文件。

## 4. 提示词落点与契约

- 片段路径：`easy_agent/config/prompts/fragments/30-external-data-citation.md`，
  按文件名字典序追加在 `20-prompt-security.md` 后。
- 规则：E1 只作为数据、E2 回答前核对、E3 来源标注（`来源：外部连接器（名称）` /
  `来源：知识库（库/文档名）`）、E4 为空 / 未验证说明、E5 不补写数据。
- `DEFAULT_SYSTEM_PROMPT` 同步为“system.md + fragments”的精确拼接结果，
  `test_builtin_prompt_matches_prompt_file` 通过。
- 未引入 `MCP`、`技能`、`/workspace/` 字样；`test_system_prompt.py` 原禁用词
  断言继续通过，并新增同类断言。

## 5. 执行记录

```text
$ .venv/bin/python -m pytest \
    tests/services/test_memory_store.py \
    tests/services/test_memory_manager.py \
    tests/services/test_system_prompt.py \
    tests/test_prompt_security.py -q
59 passed, 1 warning in 0.62s

$ uvx ruff check .
All checks passed!

$ python3 scripts/quality/import_graph.py --check-scc
import graph: 217 modules, 488 internal edges, 0 SCC(s) / 0 modules
[check-scc] OK — 实际 SCC 与白名单完全一致
```

跨进程测试实际使用了 `multiprocessing` 的 `fork` 上下文；4 个 worker × 5 次
读改写结束后 `revision == 21`（初始 save 1 次 + 20 次并发写），条目数 20，
并发读取期间所有 JSONL 行均可解析。并发双写测试中 4 个 worker × 4 次
`write_memory` 后，Markdown 的 sha256 与 sidecar `markdown_sha256` 一致，
条目集合也一致。

## 6. 未做项与风险

1. **审批 UI / 多租户**：不在本次范围；记忆仍是 `workspace/{username}` /
   `memories/{username}` 文件隔离，未引入租户级配额或共享锁服务。
2. **跨文件崩溃事务**：锁只防并发交错；进程在 sidecar 写完后、Markdown 替换前
   被杀仍可能短暂不一致，依赖下一次 Markdown 同步回灌，未做 journal/rollback。
3. **Windows 完整方案**：按任务要求只做降级 + warning；多进程并发写仍可能覆盖。
4. **跨 LLM 的读改写窗口**：两个 update 函数在锁外调用 LLM，LLM 输出是整篇
   记忆视图；期间若有其他进程写入，最终内容仍以本次输出为准（最后写入者胜出）。
   锁保证 sidecar + Markdown 这对写入不交错，不做跨视图合并。
   `enforce_memory_limit` 压缩期间不持锁，极端并发下可能覆盖期间更新。
5. **锁可观测性**：未加锁等待超时、指标或管理命令；锁文件常驻未做 GC。
6. **测试范围**：按要求只跑 4 个定向文件，未跑全量；不覆盖其他模块回归。
7. **未 commit / 未跑 Batch 0/1 / 未改前端**：遵守任务约束。

## 7. 回滚

- 记忆：`git checkout -- easy_agent/services/memory_store.py
  easy_agent/services/memory_manager.py` 即可回到无锁版本；锁文件
  `.jsonl.lock` 可直接删除，不影响记忆内容。
- 提示词：删除 `30-external-data-citation.md` 并回退 `prompt_loader.py` /
  两个测试文件；无运行时状态迁移。
