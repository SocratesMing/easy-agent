# Easy-Agent 性能与并发审计报告（T2）

> 审计人：T2（性能与并发审计员）  
> 审计对象：`/home/sututu/code/easy-agent`（后端 FastAPI + LangGraph/DeepAgents 流式对话 + SQLite/MySQL + APScheduler + 多用户工作区；前端 `frontend-vue2`）  
> 审计方式：**只读**代码审查 + 在 WSL 中运行隔离基准/复现脚本（临时目录，未触碰仓库 `data/` 与任何业务代码/配置）  
> 环境：WSL Ubuntu-24.04，`.venv/bin/python` 3.11.15，deepagents 0.6.8 / langchain 1.3.4 / langgraph 1.2.4 / fastapi 0.136.3  
> 实测脚本：`_analysis/t2_bench.py`、`_analysis/t2_bench2.py`、`_analysis/t2_repro_hub.py`（均为审计临时脚本，可删除）  
> 时间：2026-10-17

## 一、结论摘要（≤10 条，按 ROI 排序）

1. **【P0】thinking 流每 chunk 重发全量内容（`full_content`），复杂度 O(n²)**：手工构造 71K 字符思考 → 代理层转发 **365MB SSE 字节**、服务端事件中枢保留 **1.405 亿字符（约 0.28~0.56GB 内存）**，前端还要对等量 JSON 做 parse。单条长思考即可打爆带宽/内存/浏览器。
2. **【P1，已复现】同会话并发第二次发送会“静默丢流 + 取消旧流”**：`_detached_event_stream` 复用未结束的 hub，新请求订阅到旧流历史后，旧任务被 `old.cancel()` 触发 `hub.close()` 向**新订阅者**投递 EOF；实测第二条消息收到 7 条别人的回放事件、**0 条自己的内容**即结束，而后台仍在跑（数据层继续写）。同时两条流共享同一 Agent/checkpoint/`session_messages` 最后一行，存在串写风险。
3. **【P1】HITL checkpointer 每次 checkpoint 都把整个会话全部 checkpoint 全量 pickle 写库**：`PersistentMemorySaver._flush()` 在 `put/put_writes/aput/aput_writes` 后各执行一次；而 `thread_id = session_id-message_id` 每轮新增一个 thread，MemorySaver 永不丢弃旧 checkpoint → 写放大 O(轮数²) + 内存/DB blob 无界增长。
4. **【P1】流式增量持久化每 500 字符做一次“全量消息 JSON 序列化 + 同一条 SQL 写两遍 + 事件循环内同步 DB”**：实测 50KB 回复一轮 **0.75s 纯阻塞**（其中 0.31s 是重复写）；`update_last_assistant_message` 与 `_row` 现在指向同一实现，调用方却两个都调（streaming.py 共 4 处）。
5. **【P1】会话日志双写且 O(n²)**：`SessionLogger` 每记录一条（每条工具调用都会记）就 **读入整个 JSON 文件 → append → 全量 `indent=2` 重写**；另外每轮还把整段会话历史再写一份 `logs/sessions/<ts>.json`，两者都无清理/轮转。实测单会话 120 条日志 → 文件 798KB、累计写盘约 48MB，且全部是事件循环内的同步文件 IO。
6. **【P1】同步阻塞调用大量混入 async 路由**：上传解析（PDF/OCR）、LibreOffice 转换（`timeout=180` 子进程）、`await file.read()` 全量入内存 + `write_bytes`、全部同步 DB 调用。一个 10s 的 PDF/PPT 解析或转换会让**整个 uvicorn 事件循环停摆**，所有正在流式输出的会话一起卡住。
7. **【P1】SQLite 单连接 + 全局 `RLock` + 未开 WAL/busy_timeout**：进程内所有 DB 访问被一把锁串行（实测 8 线程写吞吐 **1.00x** 无加速）；跨进程（`--workers>1`/多 Pod）共享同一库文件时，长写事务下实测 5.01s 后 `OperationalError: database is locked`。
8. **【P1】多副本部署下定时任务重复执行**：`AsyncIOScheduler` 每个进程各跑一份，`distributed_lock.enabled` 默认 **false**，而 `deploy/k8s/agent-deployment.yaml` 是 `replicas: 2` 且 ConfigMap 未开启该开关 → 同一 cron 在两个 Pod 各执行一次（双倍 LLM 成本、重复会话/消息、并发写同一工作区）。
9. **【P2】Agent 缓存不是 LRU 且 side-dict 无界泄漏**：实测 250 个会话后 `_session_agents` 被裁到 100，但 `_session_expert/_session_creator/_session_connectors/_session_edit_targets/_session_create_locks` 仍是 250 条；淘汰按插入顺序（FIFO），被“访问过”的最老会话仍会被踢。叠加“每轮记忆更新→`remove_session_agent`”，正常对话几乎**每轮都重建 Agent**（create_model + MCP + 建图 + checkpoint 反序列化）。
10. **【P2】前端每收到一个 chunk 就整条消息对象替换 + 全量 markdown 重渲染**（`renderMarkdown` 无缓存、在模板里直接调用）：与第 1 条叠加时，长回复/长思考在浏览器侧也是 O(n²) 解析 + `v-html` 全量替换，表现为越写越卡直至假死。

## 二、问题清单表

| ID | 严重度 | 类别 | 位置(file:line) | 现象与证据 | 风险/影响 | 建议修法 | 工作量 |
|---|---|---|---|---|---|---|---|
| T2-01 | P0 | SSE/内存 | `services/stream_processor.py:373-385`；`api/chat.py:221-238` | thinking 事件同时携带 `content`（增量）与 `full_content`（本 step 全量），每个 chunk 都重发全量；`_StreamHub.history` 保存每个 SSE frame 字符串。实测 4000 chunk/71K 思考 → `full_content` 累计 1.405 亿字符、SSE 365MB（见 §3.1） | 单条长思考：带宽数百 MB、服务端内存数百 MB、前端 parse/渲染卡死；客户端断开后历史仍保留 120s；慢消费者还会被 `QueueFull` 直接 EOF | 去掉 `full_content`（前端本来就同时支持增量 append）；若必须保留“可幂等重放”，改为**按 1~2s 节流**下发快照，或事件里带 `step`+`seq` 让前端按需拉取；hub 增加**字节上限**（如 4MB/会话）+ 超出后停止累积仅转发 | M |
| T2-02 | P1（可上调 P0） | 并发正确性 | `api/chat.py:173-306`；`services/session_runtime.py:41`；`services/agent_manager.py:540-557` | 同一进程内 `acquire_stream` 的 owner 相同 → **不报忙**（实测 M9.1）；`_detached_event_stream` 对同会话复用未 done 的 hub；`register_stream_task` 直接 `old.cancel()`，旧任务 `hub.close()` 的 EOF 会发给**新订阅者**。复现实测：新请求 0 条自身内容即 EOF | 双击发送/双标签页/刷新后旧流仍在跑 → 新消息“无回复”、旧回复被截断；两条流共享同一个 `EasyAgent`/`last assistant` 行 → 互相覆盖写坏消息；后台继续烧 LLM token | 1) 进程内同样用 `asyncio.Lock`/`_session_stream_tasks` 拒绝并发（返回 409 或排队）；2) 每个流使用**独立 hub**，hub.close 只关闭自己的订阅者；3) `register_stream_task` 不再 cancel 旧任务，改为拒绝新流；4) `/stream/live` 绑定当前流 id | S～M |
| T2-03 | P1 | DB 写放大 | `checkpoints.py:63-93,127-145`；`db/database.py:2338-2375`；`services/streaming.py:940` | 每个 checkpoint 写操作后 `_flush()`：递归复制 `storage/writes/blobs` → `pickle.dumps` → `INSERT ... ON CONFLICT UPDATE payload`（整行覆盖）。`thread_id=f"{session_id}-{message_id}"` 使每轮新增 thread，MemorySaver 保留该 thread 的**全部** checkpoint，永不裁剪 | 一个 N 步会话：DB blob 大小 ≈ 会话历史全量，写次数 ≈ 步数 → O(n²) 写；会话越长每步越慢、内存越大；多副本下各副本内存副本互相覆盖 | 只持久化**最新** checkpoint（自定义 saver 覆写 `storage` 只保留最新 N 条/每 thread 1 条）；`thread_id` 改为按会话稳定（HITL 用 interrupt id 区分）；会话结束/成功后 `release_session_checkpointer` 或裁剪；或按 README 建议启用 Postgres checkpointer | M～L |
| T2-04 | P1 | DB/事件循环 | `services/streaming.py:900-965,1023-1096,1488-1490,1656-1657`；`db/database.py:1237-1238,1417-1473` | 每累积 500 字符：`_partial_message_dict` 重建完整消息（含 blocks/thinking/工具结果）→ `_sanitize_extra_data` 全量 `json.dumps` → UPDATE；且 `update_last_assistant_message` 内部已转调 `..._row`，调用方随后又调一次 `..._row` → **同一条 UPDATE 执行两遍**。实测 50KB 回复 100 次持久化 0.44s + 重复 0.31s | 长回复/长思考时每轮数百 ms～数秒的同步 DB + JSON CPU 全部压在事件循环上，所有用户会话一起变慢 | 1) 删掉重复的 `update_last_assistant_message_row` 调用（-50%）；2) 增量持久化改为**独立列/追加写**（如 content 单列、blocks 走单独表或只在结束时写一次）；3) 节流按时间（如 ≥2s）而非 500 字符；4) 用 `asyncio.to_thread` 包 DB 调用 | M |
| T2-05 | P1 | 磁盘 IO | `utils/session_logger.py:122-137,139-219`；`services/streaming.py:1099-1129` | `_load_existing()` 读整文件 → append → `_flush()` 全量 `json.dump(indent=2)`；构造函数也 flush 一次；每条工具调用/用户消息/助手回复都调用。`streaming.py` 另把 `session.messages` 全量 + 当前回复写 `logs/sessions/<ts>.json`（每轮一个新文件、无清理）。实测单会话 120 条 → 文件 798KB、累计写盘约 48MB（Σ≈n·S/2） | 长会话写盘 O(n²)；同步文件 IO 在事件循环；`logs/sessions/` 与 `sessions_dir/` 无轮转，磁盘无界增长（且与 DB 重复存储） | 1) 改 append-only JSONL（每次只写新增 entry，O(1)）；2) 删除 `logs/sessions/*.json` 全量快照或改为开关默认关；3) 加保留天数/大小清理；4) 写文件移出事件循环 | S～M |
| T2-06 | P1 | 事件循环阻塞 | `api/sessions.py:406-462`；`api/files.py:238-279,316-322,374-389`；`services/document_convert.py:37-93`；`utils/file_parser.py:173-293,502-517` | async 路由里直接执行：`await file.read()`（全量入内存）+ `write_bytes`；`store_parsed_content → parse_file_content`（PyMuPDF/pdfplumber 全文、pytesseract OCR、antiword/catdoc 子进程 30s）；`convert_to_pdf` → `subprocess.run(..., timeout=180)`；`/agent/files/upload` **完全没有大小上限校验** | 1 个 OCR/PDF/LibreOffice 请求 = 全服务所有 SSE 卡顿数秒～数十秒；大文件上传双份内存（read 的 bytes + write）；`files.py` 上传可被单请求打爆内存 | 解析/转换统一丢 `asyncio.to_thread`/专用 `ThreadPoolExecutor(max_workers=2~4)`；上传改**流式分块落盘**并先校验大小；`files.py` 复用 `upload_limits`；解析结果缓存 | S～M |
| T2-07 | P1 | DB 并发 | `db/database.py:199-206,228-247`；部署：`deploy/k8s/agent-deployment.yaml:27`、`docker/entrypoint.sh:77`、`web_runner.py:22` | SQLite 全进程一条连接（`check_same_thread=False`）+ 全局 `threading.RLock` 包住整个事务；**未设置** `PRAGMA journal_mode=WAL / busy_timeout`（实测 `journal_mode=delete`，默认 5s busy timeout）。实测：8 线程写吞吐 1.00x（完全串行）；4 进程写正常但长写事务下另一进程 5.01s 后 `database is locked` | 单进程 DB 吞吐封顶；`--workers>1` 或多 Pod 共享 SQLite 时随时可能 500；任何慢 DB 调用阻塞整个事件循环 | 1) 立即 `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=...; PRAGMA synchronous=NORMAL`；2) 连接池/每线程连接（WAL 下读不阻塞写）；3) 多 worker/多副本部署强制 MySQL（代码已禁止降级，但文档/部署需明确）；4) 写路径批量化 + 移出事件循环 | M |
| T2-08 | P1 | 调度/多副本 | `services/scheduler.py:102-108,125-148,193-226,229-252`；`config.py:313`；`app.py:159-168,330`；`deploy/k8s/agent-deployment.yaml:27` | 每个进程 `_start_scheduler()` + `reload_all_tasks()` 注册全量 cron；`DistributedLockConfig.enabled=False` 默认；k8s 副本=2 且 ConfigMap 未设置 `DISTRIBUTED_LOCK_ENABLED`。锁只有到执行时才抢，且未开=恒 True | 同一 cron 被 N 个副本各执行一次：N 倍 LLM 成本、重复建会话/消息、并发写同一 workspace；`misfire_grace_time=300, coalesce=True` 只解决同进程堆积 | 1) 多副本部署**强制**开启 `distributed_lock.enabled=true`（或启动校验 replicas>1 时报错）；2) 锁提前到“注册/触发前”并缩短 TTL；3) 用 APScheduler 持久 jobstore（MySQL/SQLite 表）+ 单实例 leader 选举；4) 在 README/helm values 中标注 | S～M |
| T2-09 | P2 | 缓存/内存 | `services/agent_manager.py:19,93-107,397-400,411-435,438-453` | 淘汰循环 `while len>100: victim=next(iter(dict))`：按插入顺序（**FIFO，非 LRU**，缓存命中不 move-to-end）；只 pop `_session_agents`，其余 5 个 side dict 不清理；`_session_create_locks` 也无淘汰。实测 250 会话后 side dict 均 250 条 | 长期运行内存缓慢泄漏（连接器/专家/锁对象）；“热点老会话”被误踢 → 下次请求重建 Agent（MCP 加载 + 建图 + checkpoint 反序列化，见 §3.9） | 1) 用 `OrderedDict` 命中即 `move_to_end`；2) 淘汰时同步清理 `_session_expert/_session_creator/_session_connectors/_session_edit_targets/_session_create_locks`；3) 锁对象在无等待者时回收；4) 可考虑按用户配额 | S |
| T2-10 | P2 | 前端渲染 | `frontend-vue2/src/components/ChatMessage.vue:742-744`（模板 90/111/175）；`frontend-vue2/src/mixins/chatSession.js:39-46,960-984,1021-1045` | 每个 chunk 都 `setReactive(this.messages, idx, {...msg})` / 替换 blocks 数组，触发 ChatMessage 重渲染；`renderMarkdown` 在模板 `v-html` 中直接调用、无 memo → 每帧对**全文**重新 marked + highlight | 长回复时浏览器 CPU/内存 O(n²)，与 T2-01 叠加直接卡死；`v-html` 全量替换导致选区/滚动跳动 | 1) `renderMarkdown` 加按 (content, citeNumberMap) 的缓存/`computed`；2) 流式期间用纯文本/轻量渲染，结束后一次 markdown；3) chunk 合并渲染（rAF/50ms 节流）；4) 只对“正在变化的 block”做局部更新 | M |
| T2-11 | P2 | 请求开销 | `utils/session.py:25-37`；`db/database.py:984-1025`；`api/chat.py:313-376`；`api/files.py:150-158,394-405` | `get_owned_session` 用 `db.get_session()` 做归属校验，而 `get_session` 会 `SELECT sessions.messages`（legacy LONGTEXT）+ `get_messages_from_rows` 加载**全部消息并逐条 json.loads**；`/stream/status`、`/stream/live`、文件列表/树、上传等每个请求都触发 | 长会话（几 MB 工具结果）时：任何“轻量”接口都要 10~100ms CPU/内存；首页刷新多个请求叠加；`/stream/status` 被前端轮询时尤其浪费 | 新增 `db.get_session_owner(session_id)`（只 SELECT username/workspace_name），归属校验用它；`get_session` 不再默认 SELECT legacy `messages` 列 | S |
| T2-12 | P2 | MCP 缓存/首消息延迟 | `services/mcp.py:266-277,302-391`；`services/agent_manager.py:291-320` | `_mcp_clients[key]` 以“本次缺失 server 子集 JSON”为 key 永久缓存且从不 close/清理；每个新会话首次建 Agent 都 `await get_mcp_tools` → 未命中按 server 逐个连接（stdio 会起子进程/网络连接） | 首次消息延迟随连接器数量线性增长（秒级）；按会话选择不同连接器组合会产生大量 client 对象（内存泄漏）；多副本 × N 会话重复连接 | 1) client 缓存按单个 server 分组（复用）；2) 加**上限+LRU 淘汰**并 close；3) 预热/后台加载连接器工具；4) 把 MCP 工具加载从“建 Agent 同步路径”剥离为可超时/可缓存的共享层 | M |
| T2-13 | P2 | 内存/背压 | `api/chat.py:199-238,256-306` | `_StreamHub.history` 只限条数（10000）不限字节；`subscribe()` 队列容量 = `len(history)+1000`；慢消费者 `QueueFull` 时直接 unsubscribe + EOF（**无重试**）。实测单流 134MB/0.28~0.56GB（见 T2-01） | 单条长流占用数百 MB；网络慢的移动端会被判“慢消费者”直接断流且前端无感；多会话并发时内存线性叠加 | 见 T2-01：字节上限 + 事件压缩（去掉 full_content）+ 慢消费者重连/降级策略 | S～M |
| T2-14 | P2 | 后台任务 | `knowledges/runtime/worker.py:102-126`；`knowledges/runtime/lifecycle.py:56-63` | 每 5s 轮询 `knowledge_documents` 待处理文档；每个副本各起一个 worker（无 leader 选举/分布式锁），每个副本都会对同一批文档调 Ragflow 刷新 | N 副本 = N 倍 Ragflow API 调用与 DB 查询；上游限流时互相拖慢 | 用 `distributed_lock` 或 leader 选举保证单副本轮询；延长轮询间隔/按 base 分片 | S |
| T2-15 | P2 | 数据一致性/IO | `db/database.py:987-1004,1075-1091,1038-1051`；`api/chat.py:458-466` | 消息已迁移到 `session_messages` 行表，但 `get_session` 仍固定 `SELECT sessions.messages`（legacy LONGTEXT，老会话可达数十 MB）；`update_session`（改标题）仍 `json.dumps(messages)` 回写整列；`list_sessions` 用相关子查询做 `COUNT(*)` | legacy 大字段每次会话读取都白读一遍；标题更新会重写大 blob（锁表/写放大）；会话列表 50 行 × 子查询 | 读取时不再 SELECT `messages`（仅在 rows 为空时才按需回退查）；`update_session` 拆出 `update_session_title/meta`；`message_count` 改为维护列或一次性 GROUP BY | S |
| T2-16 | P2 | 线程池/成本 | `services/streaming.py:724-805`；`services/memory_manager.py:233-320,377-463` | 每轮结束后**无条件**提交 2 个后台任务（会话记忆 + 长期记忆），每个内部同步 `llm.invoke`（额外 2 次 LLM 调用）；`run_in_executor(None, ...)` 用默认线程池（与文件解析/终端 select 共享、队列无界）；会话记忆更新成功即 `remove_session_agent` → Agent 缓存每轮失效 | 每轮多 2 次 LLM 调用（成本 + 并发费用），默认线程池被 LLM 慢调用占满会拖住其他 `to_thread` 任务；Agent 每轮重建（见 T2-09） | 1) 记忆更新加节流/条件（如每 N 轮或内容有变化）；2) 用独立 `ThreadPoolExecutor(max_workers=2~4, 有界队列)`；3) 更新成功后不必驱逐 Agent，改为标记“记忆文件 mtime”在下次请求懒加载或增量注入 | M |
| T2-17 | P3 | 全局状态 | `app.py:191`；`summarization.py:44-70`；`api/terminal.py:262-282` | lifespan 里进程级 `os.chdir(project_root)`（所有相对路径依赖它）；`configured_summarization` 在 `_create_agent()` 期间**临时替换 `deepagents.graph.create_summarization_middleware` 模块全局**；`terminal_ws` 在 async 进程里 `os.fork()` | 若未来把建 Agent 移到线程/多事件循环，全局 patch 与 fork 会互相踩（fork 多线程进程可死锁）；`os.chdir` 影响所有用户相对路径 | 1) 建 Agent 串行化或改用 `unittest.mock.patch` 之外的显式参数注入（跟踪上游是否已支持）；2) terminal 改 `subprocess`+pty 或独立 `pty.fork` 守护进程；3) 路径统一绝对化，去掉 chdir 依赖 | M |
| T2-18 | P3 | 启动/日志 | `initialization/log.py:68-88`；`app.py:184-353` | 日志 handler 为同步文件写，`shouldRollover` 每条日志都 `flush + fstat`（每 token/每请求多次写盘）；lifespan 内 `_init_database_and_state`（含建表迁移、可能的全表修复扫描）、`discover_skills`、公共专家扫描等均为同步阻塞 | 启动时间在 MySQL 大库/token 过期场景偏长（就绪探针早于完成会 503）；高日志量时事件循环被同步 IO 拖慢 | 日志改 `QueueHandler + QueueListener` 异步写；启动重活移 `to_thread`/后台任务并让 readiness 只在完成后就绪 | S～M |

## 三、详细分析与实测

> 所有实测均在同仓库 `.venv/bin/python` 3.11.15、临时目录（`/tmp/t2bench*`）下运行；不读写仓库 `data/`。

### 3.1 T2-01 thinking `full_content` 的 O(n²) 重发（P0）

**代码**（`services/stream_processor.py:373-385`）：
```python
self.accumulated_thinking += rc
step_content = ""
for blk in reversed(self.blocks):
    if blk.get("type") == "thinking":
        blk["content"] = self.accumulated_thinking[self.thinking_step_start_len:]   # 每 chunk 全量切片
        step_content = blk["content"]                                               # 本 step 全量
        break
events.append({"type": "thinking", "content": rc,
               "full_content": step_content, "step": self.current_step})           # 全量随每个 chunk 下发
```
**链路**：`astream(messages)` → `proc.handle` → `yield format_sse(ev)`（`streaming.py:966`）→ `_detached_event_stream` → `hub.broadcast(chunk)`（`api/chat.py:277`）→ `_StreamHub.history.append`（`chat.py:223`）→ 每个订阅者队列 → SSE。
**放大点**：
1. per chunk 做一次长度 L 的字符串切片（复制）；
2. per chunk 把 `full_content` 放进 dict；
3. `format_sse` 对全量内容 `json.dumps` 一次（序列化 CPU ∝ L）；
4. hub 把整个 frame 字符串存进 `history`（L 字符）；
5. 前端收到后 `JSON.parse`（∝L）并写入 DOM。

**实测（M1/M8，临时脚本 `_analysis/t2_bench.py`、`t2_bench2.py`）**：

| 用例 | chunk 数 | 最终思考字符 | `full_content` 累计字符 | 客户端收到的 SSE 字节 | `hub.history` 保留字符 |
|---|---|---|---|---|---|
| 2,000 × 18 字 | 2,000 | 35,000 | 34,517,500（×986） | **90,784,545 B（≈86.6MB）** | 33.1M 字符 |
| 4,000 × 18 字 | 4,000 | 71,000 | 140,535,500（×1,979） | **365,070,545 B（≈348MB）** | 134.4M 字符 |

> `hub.history` 存的是 Python `str`；以中文为主时 CPython 约 2~4 B/字符 → 上表 134.4M 字符 ≈ **0.28~0.56GB 常驻内存**（流结束后还保留 `_STREAM_HUB_TTL_SECONDS=120s`，`chat.py:178`）。

**真实 chunk 粒度的影响**：放大倍数 ≈ `N/2`（N=chunk 数）。即使 provider 每 chunk 只出 2 个汉字（30K 字符 → 15,000 chunk），仅 thinking 一类事件的流量也约 `15,000 × 30K/2 × ~3B ≈ 675MB`；**结论与 chunk 大小无关，只影响常数**。这是本仓库最严重的可量化性能缺陷。

**建议**：完整内容改“节流快照”（`thinking_snapshot`，≥1s 或每 500 字符一次），并给 hub history 加字节上限；前端目前逻辑已兼容“只有增量”的旧后端（`chatSession.js:966-968` 的 else 分支），改回增量成本最低。

### 3.2 T2-02 同会话并发流：新流被旧 hub 的 EOF 关闭（P1，已复现）

**代码**（`api/chat.py`）：
```python
hub = _session_stream_hubs.get(session_id)
if hub is None or hub.done:
    hub = _StreamHub(session_id); _session_stream_hubs[session_id] = hub
queue: asyncio.Queue = hub.subscribe()          # 复用旧 hub：先回放旧流历史
...
bg_task = asyncio.create_task(_drive())
```
`_drive()` 内 `register_stream_task(session_id, bg)`（`agent_manager.py:540-557`）：
```python
old = _session_stream_tasks.get(session_id)
if old and not old.done():
    old.cancel()                                 # 取消旧流
```
旧流在 `streaming.py:1176` 捕获 `CancelledError` → 保存部分回复 → `_drive` 的 `finally` 执行 `hub.close()`（`chat.py:289-291`）→ `close()` 里 `broadcast(None)` 把 **EOF 投给所有订阅者，包括刚订阅的新请求**，然后 `self._subscribers.clear()`。

**复现实测**（`_analysis/t2_repro_hub.py`，用两个假生成器 + 真实 `_detached_event_stream`）：
```
A events=10 last='<STREAM-END>'
B events=10 last='<STREAM-END>'
B received_b_content=0 | B received_a_replay=7 | B got_EOF=True
hub.done=True hub.history=40
```
结论：B（第二条消息）只收到 A 的 7 条回放事件就 EOF，**自己的内容一条也没到前端**；而 B 的后台任务还在跑（`history` 继续增长到 40）。前端会认为请求结束。

**进程内互斥缺失的根因**（`services/session_runtime.py:41`）：
```python
if held_owner and held_until >= now and held_owner != owner:
    raise SessionBusyError(...)     # owner = hostname:pid，同进程两条流 owner 相同 → 放行
```
实测（M9）：
- 同进程第二次 `acquire_stream`：**无异常**（并发不被拒绝）；
- 子进程尝试 `acquire_stream`：`SessionBusyError: 会话 ... 正在实例 DESKTOP-...:75230 上生成`（跨进程有保护）。

**次生风险（静态推断）**：两条流共享同一 `EasyAgent` 实例与同一个 `thread_id` 规则 `f"{session_id}-{message_id}"`；持久化都写“最后一条 assistant 行”（`db.update_last_assistant_message_row` 以 `ORDER BY id DESC LIMIT 1` 定位），两流会互相覆盖同一条消息；`_maybe_rename_workspace`/`_workspace_renamed` 等实例状态也会竞争。

**建议**：进程内增加 `_inflight_sessions: set[str]`，`chat_stream` 入口即拒绝（409「会话正在生成」）或排队；新流不再复用未完成 hub；`register_stream_task` 改为返回失败而非 cancel 旧任务。

### 3.3 T2-03 checkpointer 全量快照持久化（P1）

**代码**（`checkpoints.py:63-93`）：
```python
def _flush(self) -> None:
    payload = {}
    for attr in ("storage", "writes", "blobs"):
        if hasattr(self, attr):
            payload[attr] = self._plain_mapping(getattr(self, attr))   # 递归复制全部会话 checkpoint
    self._db.save_agent_checkpoint(self._session_id, pickle.dumps(payload, protocol=5))
def put(self, *args, **kwargs):
    result = super().put(*args, **kwargs); self._flush(); return result
def put_writes(self, *args, **kwargs):
    result = super().put_writes(*args, **kwargs); self._flush(); return result
```
LangGraph 每个 super-step（模型节点、工具节点）都会 `put`/`put_writes`；`MemorySaver.storage` 结构为 `thread_id -> ns -> checkpoint_id -> checkpoint`，**不裁剪**（已核对安装包 `langgraph/checkpoint/memory/__init__.py` 的 `storage: defaultdict[str, dict[str, dict[str, ...]]]` 定义）。`services/streaming.py:940` 用 `thread_id = f"{session_id}-{message_id}"`——每轮一条新 thread，因此该会话的所有历史 thread/checkpoint 永久累积。结果是：
- 每次 checkpoint 写 = 全量 pickle + 全量 DB blob 覆盖 → 总写量 O(步数²)；
- `_CHECKPOINTERS`（上限 200，任意淘汰）内的 MemorySaver 常驻内存随会话数 × 轮数增长；
- `agent_checkpoints` 行只增不减（`release_session_checkpointer` 只在删除会话时调用，`api/sessions.py:296`）；
- 下一个请求建 Agent 时 `_hydrate()` 要 `pickle.loads` 整个 blob。

**实测说明**：M6' 用合成 checkpoint（消息体较小）测得 30 次 put：payload 287B → 6,614B（23 倍增长）、总 0.1MB，验证“每次写全量、随状态增长”的机制；真实 LangGraph 消息体（含工具结果）远大于此，**O(n²) 放大主要按代码静态推断**。另 M6' 确认 `storage` 保留全部 30 个 checkpoint（threads=6）。

**建议**：`_flush()` 只保留“最新 checkpoint + 最新 writes”而不是全部；thread_id 按会话固定（每轮用 `checkpoint_id` 区分，HITL 恢复用 interrupt id）；超过 N 轮后裁剪旧 checkpoint；长期方案用官方 `AsyncPostgresSaver`（`app.py:311-321` 已支持，只是缺省没开）。

### 3.4 T2-04 增量持久化的重复写与全量序列化（P1）

**代码**（`services/streaming.py:962-966`，`1487-1491` 同）：
```python
elif ev_type == "content":
    if len(proc.accumulated_response) - last_persisted_len >= 500:
        last_persisted_len = len(proc.accumulated_response)
        _persist()
```
`_persist` → `_partial_message_dict`（全量 content/thinking/blocks）→ `db.update_last_assistant_message`；随后 `streaming.py:1095-1096`（以及 1023-1024、1192-1193、1656-1657）：
```python
db.update_last_assistant_message(session_id, assistant_message)
db.update_last_assistant_message_row(session_id, assistant_message)   # 与上一行等价，重复执行
```
`db/database.py:1237-1238` 已经 `self.update_last_assistant_message_row(...)`，即**两次完全相同**的 `_sanitize_extra_data`（全量 `json.dumps`，最大 5MB 渐进裁剪，`database.py:1330-1373`）+ 两次 `SELECT last row` + 两次 `UPDATE` + 两次 `UPDATE sessions.updated_at`。

**实测（M3）**：模拟 50KB 回复按 500 字符节奏增量持久化（消息含 20K 思考 + 5×5KB 工具结果）：

| 指标 | 数值 |
|---|---|
| 100 次单写总耗时 | 0.44s（max 6ms/次） |
| 重复调用的额外耗时 | 0.31s |
| 合计阻塞事件循环 | **0.75s/轮** |
| 单次持久化 @5KB/25KB/50KB | 5.1ms / 4.2ms / 5.5ms |
| `get_session` × 3（每轮 chat.py:446 + streaming.py:924/1107 各一次） | 约 1.2ms/次（32 条消息、1.4MB DB） |

50KB 回复即 0.75s，100~200KB 长回复/长思考（thinking 也进 blocks 全量序列化）会到数秒，且全部发生在**事件循环线程**。

**建议**：删重复调用（立刻 -50%）；把 `blocks`/`thinking` 拆成单列或单独表、增量只更新 text 列；按时间节流；`asyncio.to_thread` 包裹。

### 3.5 T2-05 日志双写 O(n²)（P1）

**代码**（`utils/session_logger.py:122-137`）：
```python
def _load_existing(self):
    if not self.log_file.exists(): return None
    with open(self.log_file, encoding="utf-8") as f:
        return json.load(f)                    # 每次实例化读整个文件
def _flush(self):
    self._data["updated_at"] = datetime.now().isoformat()
    with open(self.log_file, "w", encoding="utf-8") as f:
        json.dump(self._data, f, ensure_ascii=False, indent=2)   # 全量重写
```
`__init__` 末尾也 `self._flush()`；`log_user_message`/`log_assistant_response`/`log_tool_call` 每次 append 后立即 `_flush()`。工具调用每条都记（`streaming.py:219-229`），且 `_handle_tool_result_event` 在流式循环里同步调用 → 事件循环内同步文件 IO。

**实测（M5）**：15 轮 ×（1 用户 + 6 工具 + 1 助手）= 120 条记录：最终文件 **798KB**、最后一次记录耗时 4.0ms；按线性增长估算累计写盘 `Σ ≈ n·S/2 ≈ 48MB`（仅一个会话）。

**第二份重复日志**（`streaming.py:1099-1129`）：每轮把 `session.messages`（**全量历史**）+ 当前回复写一个新文件 `logs/sessions/<ts>_<sid>.json`，`indent=2`；全仓库无任何清理逻辑（grep 只有创建没有删除）。100 轮、历史 1MB 的会话 → 累计约 50MB 级重复数据。

**建议**：JSONL append-only；`logs/sessions` 快照默认关闭或按天清理；写文件移出事件循环。

### 3.6 T2-06 事件循环阻塞（P1）

| 位置 | 阻塞内容 | 典型耗时 |
|---|---|---|
| `api/sessions.py:406-428` | `await file.read()` 全量读入内存 + `file_path.write_bytes(content)` | 10MB 文件 ≈ 2×10MB 内存 + 磁盘 |
| `api/sessions.py:451-462` | `store_parsed_content → parse_file_content`（PDF/DOCX/OCR/antiword 子进程） | PDF 数百页数秒；图片 OCR 数秒～十几秒；doc 子进程 timeout=30s |
| `api/files.py:316-322` | `/parse` 直接同步 `parse_file_content` | 同上 |
| `api/files.py:374-389` | `convert_to_pdf` → `subprocess.run(timeout=180)`（LibreOffice 冷启动 3~20s） | 秒级 |
| `api/files.py:238-279` | 上传无 `upload_limits` 校验、`file.read()` + `write_bytes` | 无上限内存 |
| `utils/file_parser.py:243-293` | `fitz.open` / pdfminer / pdfplumber 全文提取，全内存 | 与文件大小线性 |

uvicorn 单事件循环：上述任一调用执行期间，**所有** SSE 流、健康检查、其他请求全部暂停。这在“上传附件→立刻提问”的主流程上必现（`api/sessions.py` 的上传解析在返回前完成）。

**建议**：解析/转换放专用线程池（有界）并给前端“解析中”状态；上传流式落盘 + 先校验大小；`files.py` 复用 `upload_limits`。

### 3.7 T2-07 SQLite 配置与多 worker（P1）

**代码**（`db/database.py:199-206`、`228-247`）：
```python
if self._connection is None:
    self._connection = sqlite3.connect(self.db_path, check_same_thread=False)  # 无 timeout/busy_timeout
    self._connection.row_factory = sqlite3.Row
...
with self._sqlite_lock:                 # 全进程串行
    conn = self._get_connection()
    try: yield conn; conn.commit()
    except: conn.rollback(); raise
```
- 无 `PRAGMA journal_mode=WAL`、无 `PRAGMA busy_timeout`（实测 M3.0：`journal_mode = delete`，sqlite3 默认 timeout=5s）；
- 单连接被所有线程共享，靠一把 `RLock` 串行（**M10：8 线程 × 50 次 add_message，相对顺序写 200 次，加速比 1.00x**，单次 2.5ms）；
- 跨进程：`--workers>1`（`web_runner.py:22` 支持）或多 Pod 共享同一 SQLite 文件时，`journal_mode=delete` 下读写互相阻塞；M4.2：一个进程持有写事务 6.5s，另一进程写操作在 **5.01s 后 `OperationalError: database is locked`**（到达 sqlite3 默认 busy timeout 边界）。

**建议**：启动即 `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=10000`；每线程/池化连接；多 worker 强制 MySQL；DB 调用移出事件循环。

### 3.8 T2-08 多副本定时任务重复（P1）

`app.py:330` 在 lifespan 内 `_start_scheduler()`：`init_scheduler(); scheduler.start(); reload_all_tasks()`（`app.py:159-168`）——**每个进程**都注册全部 enabled cron。`scheduler.py:241` 执行时才抢 `distributed_lock`；`config.py:313` `enabled: bool = False`；`deploy/k8s/agent-deployment.yaml:27` `replicas: 2`，ConfigMap（第 1-8 行）只设了 `AGENT_ENV/EASY_TRUST_PROXY/EASY_SKIP_LLM_PROBE`，未设 `DISTRIBUTED_LOCK_ENABLED`。

→ 同一 cron 到点，两个 pod 各执行一次（各自 `create_session` + `agent.ainvoke` + 写 run 记录），成本 ×2 且写同一 workspace。`coalesce=True/max_instances=1` 只保证**单进程内**不重叠。

**建议**：多副本模板强制 `distributed_lock.enabled=true`（或在 `replicas>1` 部署文档/启动检查里强制）；更稳妥是 leader 选举或持久化 jobstore。

### 3.9 T2-09 Agent 缓存：FIFO + side dict 泄漏 + 每轮重建（P2）

**实测（M7）**：连续创建并淘汰 250 个会话：
```
_session_agents=100 (cap 100) | _session_expert=250 | _session_creator=250 |
_session_connectors=250 | _session_edit_targets=250 | _session_create_locks=250
FIFO eviction check: touched oldest key 'k0' still present=False (LRU would keep it)
```
`_evict_agent_cache`（`agent_manager.py:101-107`）只 pop `_session_agents`，其它 4 个 dict 与锁表无清理；淘汰顺序是 `next(iter(dict))`=插入顺序，缓存命中不会 move-to-end。

**每轮重建**：`_submit_memory_updates`（`streaming.py:724-805`）在每轮结束后提交两个后台 LLM 任务；会话记忆 job 成功后 `remove_session_agent(session_id)`（`streaming.py:759-767`），而 `update_memory_after_session` 只要 LLM 返回非空就 `return True`（`memory_manager.py:233-320`）→ **正常对话几乎每轮都把 Agent 缓存清掉**。下一轮 `get_or_create_agent_for_session` 重建成本包括：`create_model`、MCP tools（`mcp.py:302-391`，未命中时逐个 server 连接）、`_build_backend`（含 2 次技能目录扫描）、`create_deep_agent` 建图、`get_session_checkpointer`（`checkpoints.py:127-145`）→ `pickle.loads` 整个会话 checkpoint blob、`_discover_user_skill_names` 多次扫目录。冷启动首消息延迟体感即来自此链路。

### 3.10 T2-10 前端 O(n²) 渲染（P2，静态 + 代码确认）

`ChatMessage.vue:742-744`：
```js
renderMarkdown(content) {
  if (!content) return ''
  return renderMarkdownHtml(linkKnowledgeCitations(content, this.citeNumberMap), renderer)
},
```
模板 `:90/:111/:175` 直接 `v-html="renderMarkdown(block.content)"`（无缓存 computed）。`mixins/chatSession.js:960-984` 每个 thinking 事件 `setReactive(this.messages, idx, {...this.messages[idx]})`；`1021-1045` 每个 content 事件改 block 后 `touchBlocks()`。→ 每个 chunk 触发整条消息重渲染 + 全文 marked/highlight。与 T2-01 的 full_content 叠加时前端需要解析并渲染数百 MB。

### 3.11 T2-11 归属校验触发全量历史加载（P2）

`utils/session.py:25-37`：
```python
def get_owned_session(db, session_id, username):
    session = db.get_session(session_id)
    if not session or (session.username or "") != (username or ""):
        raise HTTPException(404, ...)
```
`db.get_session`（`database.py:984-1025`）每次都 `SELECT ... messages ... FROM sessions`（legacy LONGTEXT）+ `get_messages_from_rows`（全部消息逐条 `json.loads`）。`/stream/status`（刷新页面轮询）、`/stream/live`、`/files/session/..`、`/workspace/tree`、上传等每请求一次 → 长会话时轻量接口也昂贵。建议加 `get_session_owner()` 只查 `username/workspace_name`。

### 3.12 T2-12 MCP 客户端缓存无上限（P2）

`mcp.py:266-277`：
```python
_mcp_clients: dict[str, MultiServerMCPClient] = {}
def _client_for(servers):
    key = json.dumps(servers, sort_keys=True, ...)   # 按「缺失 server 子集」做 key
    client = _mcp_clients.get(key)
    if client is None:
        client = MultiServerMCPClient(servers); _mcp_clients[key] = client
```
不同会话选择不同连接器组合 → 新 key → 新 client，永不关闭/淘汰；mtime 变化的旧 client 也保留。叠加 T2-09 的每轮重建，首次消息会反复 `await get_mcp_tools` 逐 server 连接（stdio 起子进程）。

### 3.13 T2-13 hub 内存与背压（P2）

`chat.py:199-238`：`subscribe()` 队列 `maxsize=len(self.history)+1000`（历史长时分配巨大队列）；`history` 只裁条数不裁字节；`QueueFull` 时 `unsubscribe + 投 EOF`。结合 T2-01：一条 134MB 的流会让服务端常驻同等内存，并让网络慢的客户端在积压 ~1000 帧时被服务端主动断开（用户看到“回复突然结束”）。

### 3.14 T2-14 知识库 worker（P2）

`knowledges/runtime/worker.py:102-126` 每 `poll_interval_seconds`（默认 5s）查 `knowledge_documents` 待处理并逐 base `refresh_base_documents`；每个副本各起一个（`lifecycle.py:56-63`），无 leader/分布式锁 → N 倍上游调用。

### 3.15 T2-15 legacy JSON blob 双读/回写（P2）

消息已双写到行表，但 `get_session` 仍固定 `SELECT messages`（`database.py:987-994`）；`update_session`（改标题，`chat.py:458-466`）仍把 `messages` 序列化回写整列（`database.py:1075-1091`）。老会话 blob 可达数十 MB（代码注释自述），每次读会话白读一次、每次改标题重写一次。

### 3.16 T2-16 后台线程池与每轮 2 次额外 LLM 调用（P2）

`streaming.py:771-772`、`801-802` 用 `loop.run_in_executor(None, job)`（默认 ThreadPoolExecutor，min(32, cpu+4)，无界队列，与文件/终端等共享）；两个 job 内部都是同步 `llm.invoke`（`memory_manager.py:233-463`）。每轮对话结束 → 2 次额外 LLM 调用。建议节流（每 N 轮或 memory 内容变化才更新）、独立小线程池、成功后不驱逐 Agent。

### 3.17 T2-17 全局状态（P3）

- `app.py:191` `os.chdir(project_root)`：进程级副作用（`web_runner.py:26`、`landlock.py:232` 在子进程内 chdir 无碍；`api/terminal.py:277` 在 fork 子进程内也无碍）；
- `summarization.py:44-70` 临时替换 `deepagents.graph.create_summarization_middleware` 模块全局，仅在建 Agent 的同步调用期间生效；当前单事件循环下无并发，但一旦建 Agent 进线程池就会互相踩；
- `api/terminal.py:262-282` 在 async 服务进程里 `os.fork()`（多线程进程 fork + 子进程 exec 之间只调用了少量函数，实践可用但属高危模式）。

### 3.18 T2-18 启动与日志（P3）

`initialization/log.py:68-88` 的 handler 在 `shouldRollover` 里每条日志 `flush()+fstat()`；所有 logger 同步写盘（无 QueueHandler）。启动 `app.py:184-353` 在 lifespan 内同步执行建表迁移/目录创建/技能扫描/公共专家扫描/`_check_llm_connection`（生产会跳过）。`init_tables` 已有 schema 版本快速路径 + 修复扫描优化（`database.py:622-659`），大库首次启动仍可能较久。

## 四、实测数据汇总（可在 WSL 复跑）

```bash
cd /home/sututu/code/easy-agent
.venv/bin/python -u _analysis/t2_bench.py     # M1~M7
.venv/bin/python -u _analysis/t2_bench2.py    # M2'/M6'/M8'/M9'/M10
.venv/bin/python -u _analysis/t2_repro_hub.py # T2-02 复现
```

| 编号 | 测项 | 关键数字 |
|---|---|---|
| M1 | thinking full_content | 2,000 chunk/35K → `full_content` 3,451 万字符、SSE 90.8MB；4,000/71K → 1.405 亿字符、SSE 365MB |
| M8 | hub 历史保留 | 2,000 帧 33.1M 字符、4,000 帧 134.4M 字符；含 `json.dumps` 的流循环 0.24s/0.63s |
| M3 | 增量持久化 | 100 次（50KB 回复）0.44s + 重复写 0.31s = **0.75s**；单次 4~6ms；DB 1.4MB；`get_session` 1.2ms |
| M4 | SQLite 跨进程 | 4 进程 × 40 写 0.87s 无错；长写锁 6.5s 时另一进程 **5.01s 后 database is locked** |
| M10 | SQLite 进程内 | 8 线程写 **加速比 1.00x**（全局 RLock 串行），2.5ms/次 |
| M5 | SessionLogger | 120 条 → 文件 798KB，估算累计写盘 ≈48MB，最后一条 4.0ms |
| M7 | Agent 缓存 | agents=100 / side dict=250；FIFO 淘汰会踢掉刚访问过的最老会话 |
| M9 | 会话流锁 | 同进程第二次 acquire 无异常（并发不拦）；跨进程会 `SessionBusyError` |
| Repro | 同会话并发流 | 新请求 0 条自身内容、收到 7 条旧流回放后 EOF；旧流被 cancel |
| M2' | 字符串累积 | dict item `+=` 比 `join` 慢 **36~83×**（40~100KB 时 8~9ms）；`str +=` 约 4× |

## 五、不确定项 / 需 Lead 复核

1. **T2-01 的真实 chunk 粒度**：未接真实 DeepSeek 流测量；按代码公式放大倍数为 N/2，无论 chunk 大小结论成立，但“单条回复 365MB”是 18 字/chunk 假设下的值；若线上 provider 每 chunk 1 字，30K 思考约 450MB 量级（更大），建议 Lead 用真实模型跑一条长思考抓包确认量级。
2. **T2-02 的前端触发面**：后端无互斥已实测；“双击发送/双标签页是否真的会走到第二条 POST”取决于前端 `canSend`/流式态 UI 守卫（未展开验证）；刷新后 `/stream/live` 重新挂载是否算并发流也未在浏览器验证。数据串写风险为静态推断（同一 last-assistant 行 + 共享 agent）。
3. **T2-03 的写放大倍率**：M6' 为合成 checkpoint（消息体小，payload 仅到 6.6KB），真实 LangGraph channel values 更大；O(n²) 来自 `_flush` 全量 pickle 的代码结构，未用真实 agent 跑 50 步量测。
4. **MySQL 路径未实测**（本机无 MySQL）：`PooledDB(maxconnections=pool_size+max_overflow, blocking=True)`、`maxusage=pool_recycle`（把“连接回收时间”传给了 `maxusage`，语义可疑，`db/database.py:166-184`）、长事务/连接泄漏需在 MySQL 环境验证。
5. **多副本行为**：k8s 清单是 2 副本 + 共享 MySQL/RWX workspace 的设计；本报告只静态核对清单与默认配置，未起 2 副本实测重复调度/checkpoint 互相覆盖。
6. **启动耗时**：未在真实大库（数百会话/MB 级消息）上量测 `init_tables`、`_repair_missing_message_rows`、技能扫描的启动时间。
7. 本次审计未修改任何业务代码/配置/生产数据；`_analysis/t2_*.py|sh` 为临时脚本，可随时删除（实测数据已在本文留档）。
