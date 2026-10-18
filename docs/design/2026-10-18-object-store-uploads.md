# 对象存储 A：上传原始文件使用 LangGraph StoreBackend

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 范围 | **仅后端**；仅「上传原始文件」（`data/uploads` 对应能力） |
| 开关 | `EASY_UPLOAD_BACKEND=local \| store`（默认 `local`） |
| 存储 | 官方 `deepagents.backends.StoreBackend` + `easy_agent.stores.get_agent_store()`（`AsyncPostgresStore` 或业务库 `SqlStore`） |
| 不涉及 | S3/MinIO/boto3、`EASY_FILE_BACKEND` 语义、workspace/生成文件、前端 |

## 1. 背景与目标

多副本（Phase 0–3）之后，DB / SSE / run 租约已经外置，但**用户上传的原始文件仍落在
本机 `data/uploads/`**：Pod 漂移或多副本轮询时，A 副本写、B 副本读不到，下载/解析
直接 404，会话里的「重新解析源文件」也会失败。本轮把这块换成**数据库里的对象存储**，
让上传原始文件成为「写一次、任何副本可读」。

目标与非目标：

| 目标 | 非目标（本轮明确不做） |
| --- | --- |
| `/agent/files/upload` 的原始文件落 LangGraph Store | workspace 目录（`workspace/{user}/session/{id}`）外置 |
| 下载 / 解析 / 预览 / 删除 / 会话按需重解析都能读回 | 生成文件（`generated_files`，物理在 workspace） |
| `local` 默认回退，行为与改造前完全一致 | 聊天上传 `uploadfiles/`（`POST /agent/sessions/{id}/upload`） |
| Store 故障 fail-closed（HTTP 503），不静默丢文件 | S3/MinIO 适配（不引入 boto3） |

## 2. 方案选择

| 方案 | 结论 | 原因 |
| --- | --- | --- |
| S3 / MinIO | 不选 | 需新依赖（boto3）+ 新凭证/桶生命周期，本轮要的是「不加基础设施地跨副本」 |
| 复用本地盘 + RWX 卷 | 不选 | 仍需共享存储，正是要摆脱的东西 |
| **LangGraph Store（官方 `StoreBackend`）** | **选用** | 已有 `get_agent_store()`（Phase 1 引入，官方 `AsyncPostgresStore`/业务库 `SqlStore`）；官方 Backend 协议直接提供读写/列举/删除 + 二进制 base64 语义，不需要自造抽象 |

`StoreBackend` 的两个关键约束与对策：

1. **默认要求 graph runtime**（`get_store()` / `get_runtime()`）。本轮的写入发生在 HTTP
   请求里、读取发生在 `asyncio.to_thread` 线程里，都不在 LangGraph 图执行上下文内。
   对策：显式传 `store=get_agent_store()`，并用**不读 Runtime** 的命名空间工厂
   （`lambda _runtime: ("uploads", safe_username)`）。已由
   `tests/services/test_upload_storage.py` 在无图环境下覆盖。
2. **命名空间分量只允许 ASCII 安全字符**（`^[A-Za-z0-9\-_.@+:~]+$`，防通配符注入）。
   `Config.sanitize_username()` 是 Unicode 感知的（中文用户名会原样保留），直接当
   命名空间会 `ValueError`。对策：非 ASCII 用户名映射成稳定别名
   `u_<sha256(username)[:16]>`（见 §4.1 的 `safe_username()`）。

## 3. 存储模型

```
namespace = ("uploads", safe_username)
key       = "/{saved_name}"           # saved_name = "{file_id}_{原文件名}"
value     = {"content": str, "encoding": "utf-8" | "base64", "created_at": ..., "modified_at": ...}
URI       = store://uploads/{safe_username}/{saved_name}
```

- 一个文件 = Store 里一个 item：命名空间按用户隔离，key 是扁平文件名（不做多级目录），
  这样 `StoreBackend.ls("/")` / `delete("/{name}")` 的语义最直观。
- 文本文件（`.txt/.md/.csv/...` 且是合法 UTF-8）按 `utf-8` 存；其它（PDF/DOCX/图片）
  自动按 `base64` 存 —— 这是 `StoreBackend.upload_files()` 的既有行为，往返无损。
- URI 只出现在 DB `session_files.file_path` 与接口响应里；它是**不透明路径**，
  业务代码不要自己拼。

## 4. 模块与接口

### 4.1 `easy_agent/services/upload_storage.py`

| 符号 | 作用 |
| --- | --- |
| `upload_backend(username="")` | `local` → `None`；`store` → 绑定该用户命名空间的 `StoreBackend`。用户名缺失/Store 不可用抛异常（**不允许**静默降级） |
| `upload_backend_kind(env=None)` / `store_mode_enabled()` | 解析开关；非法取值抛错（fail-closed，避免拼错后悄悄退回本地盘） |
| `save_upload_to_store(local_path, username, saved_name) -> str` | 上传字节，返回 `store://...`；写失败抛 `UploadStoreUnavailable` |
| `is_store_uri(path)` | 判定 URI；对 `store:/uploads/...`（`//` 被代理折叠）也容错 |
| `parse_upload_uri(path) -> (user, name)` | 严格解析：必须 `uploads/{user}/{name}` 三段；拒 `..`、多级路径、控制字符、超长 |
| `materialize_upload(path, username) -> Path` | 下载到缓存并返回本地路径；**跨用户直接拒绝** |
| `delete_stored_upload(path, username="") -> bool` | 删对象 + 缓存；对象本来不存在返回 `False` |
| `preflight_upload_store()` | 启动期自检：`store` 模式拿不到 Store 直接抛错（lifespan fail-closed），`local` 为 no-op |
| `safe_username()` / `store_key()` / `build_store_uri()` | 命名空间名、item key、URI 的唯一构造入口 |

异常层次（API 层统一映射）：

| 异常 | HTTP | 场景 |
| --- | --- | --- |
| `UploadStoreUnavailable` | **503** | Store 拿不到 / 写入失败 / 读取失败 |
| `UploadStoreAccessDenied` | 403 | URI 非法、跨用户 |
| `UploadStoreNotFound` | 404 | Store 里没有这个对象 |
| `UploadStorageError`（基类其它） | 500 | 非法开关值、local 模式下误调 store 函数、本地临时文件读不了 |

### 4.2 `easy_agent/api/files.py`

| 端点 | store 模式行为 |
| --- | --- |
| `POST /agent/files/upload` | 仍先 `save_upload_streaming` 流式写本地临时文件（复用既有大小限制/413 语义）→ `save_upload_to_store` → **删除本地临时文件** → DB 与返回值 `file_path` 都写 URI。写 Store 失败：删临时文件 + 503，DB 不落记录（**不留孤儿**） |
| `GET /download/{file_path:path}` | URI → `materialize_upload` → `FileResponse`；本地路径分支原样保留 |
| `POST /parse` | URI → materialize 后 `parse_file_content`；返回体 `file_path` 仍是原 URI（前端契约不变） |
| `GET /preview` | URI → materialize → 支持 `download=1`（attachment）与 `target=pdf`（复用 LibreOffice 转换） |
| `DELETE /{file_id}` | 先 `repo.get_file_record` 取 `file_path` → 删 DB 行 → 若为 URI 再尽力删 Store 对象；**Store 删除失败只 warning，不回滚 DB 删除** |

会话归属校验被**前置**到落盘之前（原本在写文件之后），避免「拒绝请求但留下孤儿副本」。
local 模式的返回结构、状态码、文件名口径完全不变。

### 4.3 `easy_agent/services/session_uploads.py`

| 函数 | store 模式行为 |
| --- | --- |
| `store_parsed_content(...)` | `parse_file_content` 之前 materialize（传 username）；失败**向上抛**，不静默写成「解析失败」 |
| `_find_source_path(...)` | DB 记录是 URI → materialize 后返回本地路径；`UploadStorageError` 原样抛（不吞成「找不到源文件」），其余异常仍按原逻辑告警 |
| 其它 | 本地路径分支一行未改；`uploads/*.txt` 正文与 `index.md` 清单仍写会话 workspace |

## 5. 读写链路

```
上传（store）
  client ──multipart──> POST /agent/files/upload
    ├─ get_user_dirs()                     # 目录仍在，store 模式最终为空
    ├─ repo.get_owned_session_meta()       # 归属校验前置
    ├─ save_upload_streaming()  ──> data/uploads/{user}/{id}_{name}   # 流式 + 限流
    ├─ save_upload_to_store()   ──> StoreBackend.upload_files()  ──> langgraph_store 表
    ├─ unlink(临时文件)
    ├─ repo.add_session_file(file_path="store://uploads/{user}/{name}")
    └─ 返回 file_path = URI

下载 / 解析 / 预览 / 会话重解析
  URI ──> materialize_upload()
        ├─ parse_upload_uri()            # 三段 + 字符白名单 + 长度
        ├─ 归属校验（URI 用户名 == 当前用户）
        ├─ 缓存命中？data/uploads_cache/{user}/{name}  → 直接返回
        ├─ StoreBackend.download_files() ──> bytes
        └─ 原子写缓存（tmp + fsync + os.replace，0600 / 目录 0700）
```

## 6. 失败语义（fail-closed）

- **开关拼错**（如 `EASY_UPLOAD_BACKEND=s3`）：`upload_backend_kind()` 抛错 →
  上传 500、启动自检抛错。绝不"当作 local"。
- **store 模式但 Store 不可用**：启动期 `preflight_upload_store()` 抛错（拒绝启动）；
  运行期上传 503 并回滚临时文件。
- **读取失败**：下载/解析/预览 503；对象确实不存在 404；跨用户 403。
- **删除失败**：只 warning（DB 记录已删，运维可重试清理），不影响用户操作。
- **local 模式**：以上分支全部不进入，行为与改造前逐字节一致（有回归用例）。

## 7. 缓存语义

- 位置：`data/uploads_cache/{safe_username}/{saved_name}`（`data/` 已在 `.gitignore`）。
- 原子写：同目录临时文件写完 `fsync` 再 `os.replace`，下载方不会读到半截文件。
- 权限：文件 `0600`、目录 `0700`（缓存里是用户原始文件，按私密数据对待）。
- 命中即返回、不校验对象是否变化：上传名含 `{file_id}`（uuid4 前 8 位），同一 URI
  对应的内容实际不可变；若未来支持"覆盖同名对象"，需要引入版本号/ETag。
- 缓存不是权威副本：删对象后缓存仍在（下次 materialize 会因缓存命中而成功），
  所以**缓存只用于读加速，不能被当作"已存在"的证据**。删除流程会主动清缓存。

## 8. 容量与限制

| 限制 | 值 | 说明 |
| --- | --- | --- |
| Store 主键长度 | `langgraph_store.item_key VARCHAR(191)` | key = `"/" + 名字`，故名字预算 190；超长按「保留 `{file_id}_` 前缀 + 扩展名」截断 |
| 单值容量 | SQLite `TEXT` / MySQL `LONGTEXT` | 理论上限很高，但**实践瓶颈是内存**（见下） |
| 编码膨胀 | base64 → +33% | 二进制文件在库里比原始字节大约 1/3 |
| 内存 | `StoreBackend.upload_files()` 需要整个文件 `bytes` | 上传时会临时占用「文件大小 × ~2.2」（bytes + base64 str + JSON） |
| 单文件上限 | 沿用既有 `upload_limits.max_file_size_mb`（未改） | **store 模式建议 ≤ 20MB**；更大的文件请改用 S3 方案 |
| 表增长 | 每个上传一行 | 备份/迁移（`scripts/db/migrate_sqlite_to_mysql.py`）会带上这些行 |
| 并发 | 无锁 | 同一 URI 并发 materialize 可能重复下载；原子写保证结果一致 |

## 9. 配置与门禁

```dotenv
# .env.dev.example / .env.test.example / .env.prod.example
EASY_UPLOAD_BACKEND=local       # local | store（上传原始文件；store=LangGraph Store 落库）
```

- `EASY_UPLOAD_BACKEND` 与 `EASY_FILE_BACKEND` **互相独立**：后者仍是 Phase 0 的
  多副本门禁占位（`easy_agent/runtime_features.py`），本轮一行未改，`store` 也不会
  让 `EASY_MULTI_REPLICA_STRICT=1` 通过。
- Store 后端仍由既有 `EASY_STORE_BACKEND` / `EASY_STORE_POSTGRES_URI`
  （缺省复用 `EASY_CHECKPOINT_POSTGRES_URI`）决定；本轮只是**消费**它。
- 未新增任何配置项到 `config.yaml`（开关只走环境变量，与 Phase 0 门禁同风格）。

## 10. 测试

| 文件 | 用例数 | 覆盖 |
| --- | --- | --- |
| `tests/services/test_upload_storage.py` | 27 | 开关/fail-closed、文本+二进制往返、base64 编码、materialize 缓存与 0600 权限、跨用户拒绝、非法 URI、单斜杠容错、长名截断、删除、中文用户名别名、local 模式 |
| `tests/api/test_files_store_backend.py` | 11 | 上传→download→parse→preview→delete 全链路（`InMemoryStore`）+ **真实 `SqlStore`（sqlite）** 往返 + 503 fail-closed + 跨用户 403 + 对象丢失 404 + local 回归 |
| `tests/services/test_session_uploads_store.py` | 7 | `_find_source_path` / `store_parsed_content` / `ensure_parsed_text` 的 materialize、本地路径短路、Store 故障与跨用户透传 |
| `tests/services/test_session_uploads.py` | 9 | 本地路径行为回归（解析落盘、清单裁剪、按需重解析、预览） |

门禁：`uvx ruff check .` 通过、`python3 scripts/quality/import_graph.py --check-scc` 0 SCC、
`python3 scripts/quality/check_double_write.py` 通过（未新增 `Database` 业务方法，新增的
`get_file_record` 在 `db/repositories/*`）。

## 11. 已知未覆盖 / 后续

1. **资产页列表（`GET /agent/files/list`，不带 `session_id`）在 store 模式下列不出
   上传文件**：该端点仍扫本地 `data/uploads/`，而 store 模式已删除本地副本。
   下载/解析/预览/删除/聊天引用均不受影响。
   后续：用 `store.search(("uploads", safe_user))` 列举，并从 item value 里回填真实
   `size`（`StoreBackend.ls()` 给的是 base64 长度，不能直接用）。
2. **workspace / 生成文件未覆盖**：`/preview`（工作区文件）、`/session/{id}` 生成
   文件列表、`/workspace/tree` 仍读本地 RWX；多副本安全仍取决于共享卷。
3. **聊天上传 `uploadfiles/` 未覆盖**：`POST /agent/sessions/{id}/upload` 仍写
   `workspace/{user}/uploadfiles/`。因此群聊场景下，若用户从会话上传，源文件仍是本地的；
   只有 `/agent/files/upload` 走 Store。
4. **历史数据未迁移**：已存在的本地 `data/uploads/**` 记录保持本地路径可读
   （`_find_source_path`/download 的本地分支仍在），没有搬迁脚本。
5. **无多副本实机验证**：本轮未起服务、未用 Postgres 版 Store 做联调，仅
   `InMemoryStore` + 业务库 `SqlStore`（sqlite）单测。
6. **未做配额/清理**：Store 里的对象只随 `DELETE /agent/files/{id}` 删除；无 TTL、
   无孤儿回收（DB 删除失败等极端场景可能残留）。

## 12. 回滚

- 运行时：`EASY_UPLOAD_BACKEND=local`（默认值）即回到改造前行为；期间已写入 Store 的
  文件仍可按 URI 读回（代码保留 store 分支），但资产页列表会看不到它们。
- 代码：回滚 `easy_agent/services/upload_storage.py`、`api/files.py`、
  `services/session_uploads.py`、`app.py` 的 preflight、`db/repositories/files.py` 的
  `get_file_record`，以及 3 个 `.env.*.example` 的一行注释；不涉及 schema、依赖、前端。
- 数据：`langgraph_store` 中 `namespace` 以 `uploads\x1f` 开头的行可整批删除
  （只影响 store 模式上传的文件）。
