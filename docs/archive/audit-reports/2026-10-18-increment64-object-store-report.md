# Increment 64 / 对象存储 A：上传原始文件落 LangGraph Store 报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 分支 | `refactor/audit-improvements-2026-10-18`（Phase 0–3 之上，未提交） |
| 范围 | 仅后端；仅「上传原始文件」（`data/uploads` 能力）；不改前端 |
| 存储 | 数据库 LangGraph Store（官方 `StoreBackend` + `get_agent_store()`），**不是 S3** |
| 未执行 | commit / push / Batch 0/1 / Web Terminal / 全量 `pytest tests/` / 启动服务 |
| 结论 | 达成：`EASY_UPLOAD_BACKEND=store` 时上传原始文件写入 Store，URI 落 DB；下载/解析/预览/删除/会话重解析全部可读回；`local` 仍为默认且行为不变；Store 故障 fail-closed 503。**未覆盖 workspace / 生成文件 / 聊天 `uploadfiles/`，资产页列表在 store 模式下列不出上传文件（见 §4）** |

## 1. 交付内容

### 1.1 新增 `easy_agent/services/upload_storage.py`（465 行）

单一适配层，业务代码只通过它接触 Store：

- `upload_backend(username)`：`local` → `None`；`store` → `StoreBackend(store=get_agent_store(),`
  `namespace=lambda _rt: ("uploads", safe_username))`。显式传 store + 命名空间工厂不读
  `Runtime`，因此 HTTP 线程 / `asyncio.to_thread` / 脚本里都能直接调用（**不依赖 graph runtime**）。
- `save_upload_to_store(local_path, username, saved_name)`：`upload_files([("/name", bytes)])`，
  返回 `store://uploads/{safe_user}/{saved_name}`；任一 `FileUploadResponse.error` 都抛
  `UploadStoreUnavailable`（不静默丢文件）。
- `is_store_uri()` / `parse_upload_uri()`：严格 `uploads/{user}/{name}` 三段；拒 `..`、多级
  路径、控制字符、超长名；对 `store:/uploads/...`（代理折叠 `//`）容错。
- `materialize_upload(path, username)`：跨用户直接 `UploadStoreAccessDenied`；下载后
  **原子写** `data/uploads_cache/{user}/{name}`（`tmp + fsync + os.replace`，文件 0600、
  目录 0700）；缓存命中直接返回。
- `delete_stored_upload(path, username="")`：删对象 + 清缓存，对象本不存在返回 `False`。
- `preflight_upload_store()`：启动期自检，`store` 模式拿不到 Store 直接抛错（fail-closed）。
- 异常层次 → HTTP：`UploadStoreUnavailable` 503 / `UploadStoreAccessDenied` 403 /
  `UploadStoreNotFound` 404 / 其余 `UploadStorageError` 500。
- 两个实现细节：
  - `langgraph_store.item_key` 是 `VARCHAR(191)`，超长保存名按「`{file_id}_` 前缀 +
    扩展名」截断（保前缀可定位、保扩展名给解析器/MIME 用）。
  - `StoreBackend` 命名空间只允许 ASCII 安全字符，而 `Config.sanitize_username` 是
    Unicode 感知的：非 ASCII 用户名映射为稳定别名 `u_<sha256[:16]>`（否则中文用户名
    上传会直接 `ValueError`）。

### 1.2 `easy_agent/api/files.py`（+130/−10）

- `POST /upload`：流式落本地临时文件（复用 `save_upload_streaming` 与其 413 语义）→
  store 模式写 Store → **删除本地临时文件** → DB 与返回 `file_path` 都是 URI；
  写 Store 失败删临时文件并 503（DB 不落记录）。local 分支不变。
- `GET /download/{file_path:path}`：URI → materialize 后 `FileResponse`。
- `POST /parse`：URI → materialize 后解析；响应 `file_path` 仍是原 URI。
- `GET /preview`：URI → materialize；`download=1` 走 attachment，`target=pdf` 复用既有
  LibreOffice 转换。
- `DELETE /agent/files/{file_id}`：先取记录 → 删 DB 行 → URI 则尽力删 Store 对象（失败只 warning，
  不回滚 DB 删除）。
- 附带：会话归属校验从「写文件之后」前置到「写文件之前」，拒绝请求不再留孤儿副本。

### 1.3 `easy_agent/services/session_uploads.py`（+24/−2）

- `store_parsed_content`：`parse_file_content` 前 materialize（传 username），失败向上抛。
- `_find_source_path`：命中 DB 的 URI → materialize 后返回本地路径；`UploadStorageError`
  原样抛（不吞成「找不到源文件」）。
- 本地路径逻辑（`uploads/*.txt`、`index.md` 清单）一行未改。

### 1.4 启动自检 `easy_agent/app.py`（+7）

lifespan 在 `start_store_backend()` 之后调用 `preflight_upload_store()`：`local` 为 no-op，
`store` 模式 Store 不可用则**启动失败**（避免跑起来后每次上传都 503）。

### 1.5 DB 读取助手（无 schema 变更）

- `easy_agent/db/repositories/files.py`：新增 `get_file_record(db, file_id, username)`
  （删除前取 `file_path`，用于判断是否要清 Store 对象）。
- `easy_agent/db/repositories/file_repository.py`：新增同名纯委托。
- **未改 schema / 未新增 `Database` 业务方法**（`check_double_write.py` 通过）。

### 1.6 配置

`.env.dev.example` / `.env.test.example` / `.env.prod.example` 各加一行：

```dotenv
# EASY_UPLOAD_BACKEND=local       # local | store（上传原始文件；store=LangGraph Store 落库）
```

`EASY_FILE_BACKEND` 语义一行未改（仍是 Phase 0 门禁占位）。

### 1.7 测试（新增 54 例）

| 文件 | 用例 | 说明 |
| --- | --- | --- |
| `tests/services/test_upload_storage.py` | 27 | 新 |
| `tests/api/test_files_store_backend.py` | 11 | 新（含真实 `SqlStore` 往返） |
| `tests/services/test_session_uploads_store.py` | 7 | 新 |
| `tests/services/test_session_uploads.py` | 9 | 新（本地路径回归） |

> 说明：任务给的验证命令里包含 `tests/services/test_session_uploads.py`，但该文件在
> 本分支基线**并不存在**（会话解析的既有覆盖在 `tests/test_file_context.py`）。为让
> 该命令原样可执行、并给 services 层一个不依赖 lifespan 的聚焦回归，本次新建了这个
> 文件（9 例，全部是本地路径行为，不改变既有覆盖）。

### 1.8 文档

- `docs/design/2026-10-18-object-store-uploads.md`（设计：模型/接口/链路/失败语义/容量/回滚）
- 本报告

## 2. 验证

### 2.1 目标测试（按要求，不跑全量）

```
pytest tests/services/test_upload_storage.py tests/api/test_files_store_backend.py        tests/services/test_session_uploads_store.py tests/api/test_files.py        tests/services/test_session_uploads.py tests/test_file_context.py -q
→ 84 passed
```

分文件：27 / 11 / 7 / 8 / 9 / 22。

补充跑了相邻的上传回归（仍非全量）：

```
pytest tests/services/test_upload_streaming.py tests/test_upload_limits.py -q
→ 10 passed
```

### 2.2 门禁

| 检查 | 结果 |
| --- | --- |
| `uvx ruff check .` | `All checks passed!` |
| `python3 scripts/quality/import_graph.py --check-scc` | `226 modules, 525 internal edges, 0 SCC(s)` / OK |
| `python3 scripts/quality/check_double_write.py` | `OK — legacy sessions.messages 写入与 Database 业务方法均已冻结`（Database 153 methods / 115 pure delegations） |

### 2.3 未做的验证

- **未跑全量 `pytest tests/`**（按要求）；改动前的基线是 1399 passed。本轮触碰的
  `api/files.py` / `session_uploads.py` 的所有既有用例（`test_files.py` 8 例、
  `test_file_context.py` 22 例）均通过，但**不能据此断言全量 1399 仍全绿**。
- **未起服务 / 未用 Postgres 版 Store 联调**，也没有多副本实机（Pod A 写、Pod B 读）
  验证。真实 DB 路径由 `test_real_sql_store_backend_roundtrip`（sqlite `SqlStore`）
  覆盖，Postgres 版 `AsyncPostgresStore` 只有既有 B1 单测覆盖。

### 2.4 关于并发工作区（重要）

执行期间观察到**工作区并非静止**：`easy_agent/agent.py`、`easy_agent/backends/context.py`
在中途出现改动（+34 / +73 行，非本增量），并出现过又消失的临时探针文件。这些改动**不是
本增量产出**，我未触碰这两个文件。上述测试/门禁均在这些并发改动出现之后重跑通过，
但若并发改动继续，最终状态需由父会话统一复核。

## 3. 关键设计决定（复盘）

| 决定 | 理由 | 代价 |
| --- | --- | --- |
| 显式 `store=get_agent_store()` + 不读 Runtime 的命名空间工厂 | 上传/下载都不在 LangGraph 图里；否则只能做成"图内工具" | 每个请求新建一个轻量 `StoreBackend`（无可变状态） |
| `store` 模式上传后删除本地临时文件 | 避免同一文件存两处、两处不一致 | 本地盘不再有"最后兜底"副本（故障时只能靠 Store） |
| Store 故障 503 而不是降级本地 | 降级会导致 DB 记 URI 但对象在本地盘，跨副本必坏；且用户以为存上了 | 单副本场景下 Store 抖动会让上传直接失败（诚实优于静默） |
| 删除 Store 对象失败只 warning | DB 行已删，回滚会让用户看到"删除失败"但记录已没了 | 可能残留孤儿对象（无回收机制） |
| 非法 `EASY_UPLOAD_BACKEND` 抛错 | 拼错后悄悄退回本地盘是最难发现的事故形态 | 配置错误时上传 500 / 启动失败 |

## 4. 风险与未做（诚实清单）

1. **资产页列表缺口**：`GET /agent/files/list`（不带 `session_id`）仍扫本地
   `data/uploads/`，store 模式下该目录为空 → 资产页看不到已上传文件。
   上传→聊天引用→下载/解析/预览/删除都正常。修复需要 `store.search` 列举 + 从 item
   value 回填真实 `size`（`StoreBackend.ls()` 返回的是 base64 长度，直接展示会偏大 1/3）。
2. **workspace / 生成文件 / 聊天 `uploadfiles/` 未覆盖**：`/workspace/tree`、`/preview`
   的工作区分支、`generated_files`、`POST /agent/sessions/{id}/upload` 仍是本地 RWX。
   多副本"文件真正安全"仍不完整。
3. **大文件**：`StoreBackend.upload_files()` 需要整个文件 `bytes`，二进制再 base64
   （+33%），瞬时内存约 `2.2 × 文件大小`。单文件上限沿用 `upload_limits`（未改），
   但 store 模式建议 ≤ 20MB；更大文件应走 S3。
4. **key 长度**：`VARCHAR(191)`，超长名截断（保 `{file_id}_` 前缀 + 扩展名）。
   SQLite 不校验长度，MySQL 才可能暴露该边界；截断逻辑有单测，但**未在 MySQL 上实测**。
5. **中文用户名别名**：非 ASCII 用户名在 Store/缓存里用 `u_<hash>`，与本地
   `data/uploads/{中文名}/` 目录名不一致（仅影响排查可读性，不影响功能）。
6. **缓存不失效**：靠上传名含 uuid 视为不可变；未来若要"覆盖同名对象"必须加版本。
7. **并发**：同一 URI 并发 materialize 可能重复下载（无锁）；原子写保证不出半截文件。
8. **孤儿对象**：上传成功但 `add_session_file` 抛错、或删除 Store 失败时，Store 里会
   残留对象；无 TTL/回收任务。
9. **无历史迁移**：既有本地 `data/uploads/**` 不上迁，老记录继续按本地路径可读。
10. **`/agent/files/parse` 的本地路径仍无归属校验**（改造前就如此，本轮未扩大范围，
    仅新增 store URI 的归属校验）。这是一个既有的越权面，建议后续单独修。

## 5. 回滚

- 运行期：`EASY_UPLOAD_BACKEND=local`（默认）立即回到改造前；已写入 Store 的文件仍可
  按 URI 读回，但没有列举入口。
- 代码：删除 `easy_agent/services/upload_storage.py`，回滚 `api/files.py` /
  `services/session_uploads.py` / `app.py` preflight / `db/repositories/files.py`
  的 `get_file_record` / 3 个 `.env.*.example` 注释行。无 schema、无依赖、无前端改动。
- 数据：`langgraph_store` 中 `namespace` 以 `uploads` 开头的行可整批清理。

## 6. 下一步建议

1. 补 `GET /agent/files/list` 的 store 分支（含真实 size），消除资产页缺口。
2. 迁移聊天 `uploadfiles/`（真正影响"用户上传"主链路）与 `generated_files`。
3. 用 Postgres 版 Store + 两副本做一次写/读/删冒烟。
4. 加孤儿对象清理任务（按 DB 反查）与 store 模式下的 `max_file_size_mb` 建议值校验。
5. 单独修复 `POST /agent/files/parse` 本地路径的归属校验。
