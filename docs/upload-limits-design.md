# 文件上传限额改造方案（单文件 10M / 单次 5 个 / 总量 20M）

> 状态：**已按第 11 章的确认口径实施完成**（2026-10-15）。
> 实施结果与偏差见文末「12. 实施记录」。
> 目标：对话输入框的附件上传增加三类限制，阈值全部来自后端配置文件。
> 适用前端：`frontend/`（Vue3）、`frontend-vue2/`（Vue2）、以及 vue2 打包出的 SDK 产物（本文统称"两套前端"）。

---

## 1. 需求与口径

| 编号 | 需求 | 语义 |
|---|---|---|
| R1 | 单个文件最大 10MB（可配） | 单文件字节数上限 |
| R2 | 每次最多 5 个文件（可配） | 输入框内**待发送附件**的数量上限 |
| R3 | 所有文件合计最大 20MB（可配） | 输入框内**待发送附件**的总字节数上限 |
| R4 | 占用/剩余判定 | 2 个文件占满 20MB → 第 3 个直接拒绝；占 18MB 剩 2MB → 第 3 个 >2MB 拒绝、≤2MB 允许 |

R4 决定了 **20MB 必须是"预算池"语义**（增量扣减、删除即释放），而不是"整批一次判定"。实现上等价于：**每次加入新文件前，用「当前列表已用额度」+「本批已接受的额度」做增量校验**。

---

## 2. 方案总览：三层校验

单靠前端挡不住（直接调 API 可绕过）；单靠后端也挡不住 R2/R3（上传接口是**单文件无状态**的，看不到"一次选择"的边界）。因此必须三层：

```
选文件            入列+上传              发送消息
[前端] ─────────► [后端 upload] ────────► [后端 chat/send]
R1+R2+R3 即时校验   R1 兜底(413)          R2+R3 兜底(422)
（用户体验主力）    （单文件权威校验）     （组合约束权威校验）
```

- **第 1 层（前端，`ChatInput.handleFileSelect`）**：责任人机体验。在**文件进入列表之前**做同步预筛，只有通过的文件才入列、才发起上传（现在是并行上传，必须在入列前判定，否则会出现"传了一半才发现超限"）。
- **第 2 层（后端，`POST /agent/sessions/{sid}/upload`）**：单文件上限的**权威校验**，超限返回 `413` + 明确 detail。
- **第 3 层（后端，`POST /agent/chat`）**：`files` 数组的个数与总量校验（`len ≤ 5`、`sum(size) ≤ 20MB`），超限返回 `422`。这是唯一能真正落地 R2/R3 的服务端位置。

---

## 3. 配置设计

### 3.1 YAML（`easy_agent/config/config-example.yaml`，新增顶层段）

```yaml
# ===== Upload Limits =====
# 对话附件上传限额（输入框「+ → 添加文件」）。
# 单位统一为 MB（1MB = 1024*1024 字节），与前端 formatSize 一致。
# 注意：若把 max_total_size_mb 调到 >100，需同步调整 nginx 的 client_max_body_size
# （docker/nginx-frontend.conf 现为 100m），否则会被 nginx 以 413 提前拦截。
upload_limits:
  enabled: ${UPLOAD_LIMITS_ENABLED:-true}              # false = 前后端都不限（联调/压测用）
  max_file_size_mb: ${UPLOAD_MAX_FILE_SIZE_MB:-10}     # R1 单文件上限
  max_files_per_message: ${UPLOAD_MAX_FILES:-5}        # R2 输入框待发送附件数上限
  max_total_size_mb: ${UPLOAD_MAX_TOTAL_SIZE_MB:-20}   # R3 待发送附件总大小上限
```

说明：
- 用 `${VAR:-默认值}` 占位符，可按环境在 `.env.{AGENT_ENV}` 覆盖（沿用项目既有约定）。
- `config.yaml` 是本地 gitignore 文件，**不加这段也能启动**（代码侧给默认值，见 3.2）；要显式配置则手工补上。
- 建议同步更新 `.env.dev.example / .env.test.example / .env.prod.example` 的变量清单。

### 3.2 后端模型（`easy_agent/config/config.py`）

```python
class UploadLimitsConfig(BaseModel):
    """对话附件上传限额（见 config.yaml 的 upload_limits 段）。"""
    enabled: bool = True
    max_file_size_mb: int = 10          # gt=0
    max_files_per_message: int = 5      # gt=0
    max_total_size_mb: int = 20         # gt=0
```

- `Config` 增加字段：`upload_limits: UploadLimitsConfig = Field(default_factory=UploadLimitsConfig)`。
- `from_yaml` 增加 `upload_limits=cls._parse_upload_limits_config(data)`（`data.get("upload_limits", {})` → `UploadLimitsConfig(**...)`），**缺失该段时用默认值**，向后兼容，不涉及 DB `SCHEMA_VERSION`。
- **加一致性自检**（启动时 `logger.warning` 或直接 fail）：
  - `max_total_size_mb >= max_file_size_mb`（否则"单文件合法但组合必然超限"，等于配额写废）；
  - 三者均 > 0；
  - 若 `max_total_size_mb * 1MB > nginx 限制` 给一条提示（nginx 是 100m 常量，可只写进注释不写校验）。

### 3.3 阀值下发接口（`easy_agent/api/settings.py`）

沿用现有"公共接口无需登录"的写法（见 `/agent/settings/models`、`/agent/settings/web-search`）：

```python
@router.get("/upload-limits", summary="获取上传限额（公共接口，无需登录）")
async def get_upload_limits():
    cfg = get_agent_config()["config"]
    ul = cfg.upload_limits
    return {
        "enabled": ul.enabled,
        "max_file_size_mb": ul.max_file_size_mb,
        "max_files_per_message": ul.max_files_per_message,
        "max_total_size_mb": ul.max_total_size_mb,
    }
```

- 前端在 `ChatInput` 挂载时拉一次（与 `loadWebSearchStatus()` 同模式）。**拉取失败时回落到内置保守默认值 10/5/20，绝不因接口失败而放行**。
- 配置改动生效时机：**后端改配置需重启**；前端改后刷新页面即取新值（无需重新构建）。

---

## 4. 前端改造（两套前端同步）

### 4.1 状态与拉取

```js
// api/settings.js 新增
export async function getUploadLimits() { /* GET /agent/settings/upload-limits */ }

// ChatInput.vue
const DEFAULT_LIMITS = { enabled: true, maxFileSize: 10*1024*1024, maxCount: 5, maxTotal: 20*1024*1024 }
const uploadLimits = ref({ ...DEFAULT_LIMITS })
async function loadUploadLimits() { /* 失败保持默认值 */ }
onMounted(loadUploadLimits)
```

### 4.2 入列前校验（核心）

```js
function pickAcceptable(files) {
  const L = uploadLimits.value
  if (!L.enabled) return { accepted: files, rejected: [] }

  let count = uploadedFiles.value.length                       // 含上传中/已完成/失败未删
  let total = uploadedFiles.value.reduce((s, f) => s + f.size, 0)

  const accepted = [], rejected = []
  for (const file of files) {
    if (file.size > L.maxFileSize)
      { rejected.push({ file, reason: 'file_size' }); continue }
    if (count + 1 > L.maxCount)
      { rejected.push({ file, reason: 'count' }); continue }
    if (total + file.size > L.maxTotal)
      { rejected.push({ file, reason: 'total', remain: L.maxTotal - total }); continue }
    accepted.push(file); count++; total += file.size
  }
  return { accepted, rejected }
}
```

- **贪心顺序 = 用户选择顺序**（`FileList` 顺序）；超限的逐个跳过，不整批拒绝（见 D4）。
- 通过 `accepted` 的文件才 `push` 进 `uploadedFiles` 并调用 `uploadOneFile()`。
- 判定在**同步代码段**内完成（基于 `uploadedFiles.value` 当前快照），避免并发/异步间隙导致超额。
- 既有 `cancelled`/abort 逻辑不受影响（校验在入列前，删除释放额度天然成立——`removeFile` 已 splice）。

### 4.3 提示 UI（需要新增，ChatInput 目前没有 toast）

`ChatInput.vue` 现在只有 `v-tip`（hover 悬浮提示），没有 toast；`ExpertListPanel.vue` 里各有一份自建 toast。建议在 `ChatInput` 内新增一个轻量提示条（两套前端同步，样式沿用"纯 `var()` + 字面量回退、不用 `color-mix`/flex `gap`"的兼容约定）：

- 位置：输入框上方（文件列表区域附近），2.6s 自动消失，可手动关闭；
- 形态：**一条汇总消息**（而不是每个被拒文件弹一次），例如：

| 场景 | 文案建议 |
|---|---|
| 单文件超限 | `xxx.pdf 12.3MB 超过单文件上限 10MB，已忽略` |
| 数量超限 | `一次最多 5 个文件，已忽略 2 个：a.pdf、b.docx` |
| 总量超限（含剩余量） | `附件总大小上限 20MB，剩余 2.0MB，已忽略 b.docx（3.4MB）` |
| 混合 | 合并成一条多行：`已忽略 3 个文件：xxx.pdf（超过 10MB）；b.docx（超出剩余空间 2.0MB）…` |
| 全部被拒 | 同上，且不上传任何文件 |

- 可选增强：文件列表旁常驻一行 `已用 12.3MB / 20MB · 3/5 个`（见 D6）。

### 4.4 上传失败原因展示（建议顺带做）

现在后端返回的 `detail`（如"文件超过大小限制"）在卡片上只显示成"上传失败"。建议在 `uploadOneFile` 的 catch 里保存 `error.message` 到 `fileItem.errorMessage`，卡片上以 title/文字展示，否则用户不知道是"超限"还是"网络错误"。

---

## 5. 后端改造

### 5.1 单文件校验（`easy_agent/api/sessions.py::upload_session_file`）

```python
ul = cfg.upload_limits
if ul.enabled:
    content = await file.read()
    if len(content) > ul.max_file_size_mb * 1024 * 1024:
        raise HTTPException(413, detail={
            "code": "UPLOAD_FILE_TOO_LARGE",
            "message": f"单个文件不能超过 {ul.max_file_size_mb}MB",
            "limit_mb": ul.max_file_size_mb,
            "actual_mb": round(len(content) / 1024 / 1024, 2),
        })
```

要点：
- **先读后判**会全量进内存（现状也是 `read()`），10MB 上限下风险可控；若想更稳，改成**分块读 + 边写边累加**，超限即中断并删除半成品文件（推荐，但会增加约 15 行）。
- 拒绝时**不要落盘**（或落盘后立即 unlink）。
- 统一错误结构（`code`/`message`/`limit`/`actual`），便于前端直接展示。

### 5.2 发送接口兜底（`easy_agent/api/chat.py`）

`ChatRequest.files` 是 `Optional[List[dict]]`（`models/api.py:71`），每项含前端传来的 `id/filename/size/file_path/type`：

```python
if request.files and ul.enabled:
    if len(request.files) > ul.max_files_per_message:  # 422
        raise HTTPException(422, detail={"code": "UPLOAD_TOO_MANY_FILES", ...})
    total = sum(int(f.get("size") or 0) for f in request.files)
    if total > ul.max_total_size_mb * 1024 * 1024:     # 422
        raise HTTPException(422, detail={"code": "UPLOAD_TOTAL_TOO_LARGE", ...})
```

- 说明：`size` 由客户端声明、可伪造；但配合 5.1 的**单文件权威校验**，"伪造 size 只可能让它更早被拒"，不会造成超额入库。若要更严，可改为按 `file_path` 去 DB 查真实记录（`db.get_session_files(session_id)`）求和 —— 成本更高，作为可选加强项。

### 5.3 资产面板上传（`easy_agent/api/files.py::upload_file`）

同一个后端还有一个"上传到工作区/资产"的入口（`POST /agent/files/upload`，AssetsPanel 在用），目前同样零校验。**是否也套用单文件 10MB 需要你确认**（见 D8）；R2/R3 属于"对话附件"概念，不建议套到资产面板。

---

## 6. 你可能没考虑到的点（重点）

1. **"20MB" 的口径必须钉死**：是"输入框里待发送附件的池子"还是"整个会话历史累计上传"？
   - 按 R4 的语感应为前者（池子/预算）；发送消息后输入框清空 → **额度自动全额重置**。
   - 若按"会话累计"理解，用户会在第 N 条消息时莫名不能传文件，且需要服务端维护状态，复杂度高，**不建议**。
2. **"5 个"的计数范围**：是"一次选择动作"还是"输入框内附件总数"？按 R4 应为**总数**（选 3 个再选 3 个，第 6 个拒绝）。
3. **失败文件是否占额度**：上一轮改动后，上传失败的文件**仍留在列表里**（只是发送时被过滤）。它现在会白占 1 个名额和对应体积。需你选：①仍占额度（用户自己点 × 释放）；②失败后自动从列表移除 + 提示（更干净，但用户看不到失败条目）；③保留条目但**不计入额度**（会导致"列表 6 个文件"看起来超限，最容易引起困惑，不推荐）。
4. **上传中的文件是否占额度**：必须占，否则并行选第 6 个就绕过了。
5. **服务端单文件校验不可省**：前端只是体验层，任何人直接调 `POST /agent/sessions/{sid}/upload` 都能绕过。10MB 必须在后端硬挡。
6. **"5 个/20MB" 服务端也没有天然位置**：上传接口是单文件无状态请求，服务端看不到"一次选择"的边界；所以我建议**在发送消息接口兜底**（这是唯一拿得到整批清单的地方）。即便如此，**用户仍可分批上传**（每次 5 个、每次 20MB 内）把服务端塞满 —— 若要严格限制"单会话未发送文件总量"，需要服务端维护状态（DB 统计 + 并发窗口处理），复杂度明显上升，**建议本次不做**（D9）。
7. **配置可能被写歪**：`max_total_size_mb < max_file_size_mb`（如单文件 10MB、总量 5MB）会让"单文件校验通过、组合必然失败"，表现为"所有文件都传不上去"，排查成本很高 → 建议启动时做一致性自检并告警（见 3.2）。
8. **阈值单位与进制**：统一 MB = 1024×1024；边界用 `>` 判定（`size == 10MB` 允许、剩余 `== 新文件大小` 允许），要用**字节数**比较，别换算成浮点 MB 再比（0.1MB 浮点误差会误判）。
9. **阈值下发通道与缓存**：配置在后端 YAML，浏览器读不到，必须由接口下发；**接口失败要有保守默认值**，否则一次 500 就等于限额失效。
10. **nginx 是另一道天花板**：`docker/nginx-frontend.conf` 的 `client_max_body_size 100m` 会在后端之前以 413 拦截；本次 20MB 总量安全，但以后调大到 >100MB 必须同步改 nginx（否则"配置改了但不生效"）。
11. **多实例/多标签页**：不同会话的输入框各自独立 20MB；同一用户开两个标签页可以同时用两份额度。这是"前端池子"口径的必然结果，需接受。
12. **提示形式要从零搭**：`ChatInput` 没有 toast 基建，需要新建（两套前端 + SDK 同步），且要遵守本项目的 IE/旧 Edge 兼容约定（纯 `var()` + 字面量回退，不用 `color-mix`/flex `gap`）。
13. **失败原因要透出**：现在后端 413 的 `detail` 前端丢弃了，卡片只显示"上传失败"。不补这一步，用户遇到超限会以为是网络问题，反而增加答疑成本。
14. **与既有其他限制的关系**（别重复造/别撞车）：知识库上传是另一套（100MB/单次 1 个，`knowledges/core/config.py`）；内容解析上限 50MB（`utils/file_parser.py`）> 新限额，不冲突；资产面板上传同后端但不同入口（见 D8）。
15. **发送前"上传中"的保护**：`send()` 现在遇到"有文件在上传中"会直接 return；新校验与它无冲突，但提示语可以顺势改得更明确（"文件正在上传，请稍候"）。
16. **上传中被删除的残留文件**（既有小瑕疵）：`removeFile` 只 abort 请求，若服务端已落盘可能残留孤儿文件。与本次限额无关，可顺带在 5.1 的"分块写 + 超限/中断清理"里一并处理。
17. **是否需要前端 `accept` 限制文件类型**：现状 `<input type="file" multiple>` 没有 `accept`，用户能选任意类型（`.exe` 等）。本次需求没提，但既然在改这块，值得你顺手决定（D10）。
18. **测试数据**：验证 R4 需要构造"占 18MB 剩 2MB"的场景；用 `fsutil file createnew`（Windows）或 `dd` 快速造指定大小文件，比找真实大文件高效。

---

## 7. 边界用例矩阵（建议直接作为验收清单）

| # | 场景 | 期望 |
|---|---|---|
| 1 | 单文件 9.9MB | 允许上传 |
| 2 | 单文件正好 10.0MB | 允许（`>` 判定） |
| 3 | 单文件 10.0MB + 1B | 前端忽略 + 后端 413 |
| 4 | 已 4 个，再选 1 个 | 允许（第 5 个） |
| 5 | 已 5 个，再选 1 个 | 前端忽略并提示"最多 5 个" |
| 6 | 一次选 6 个（都不超单文件） | 接受前 5 个，第 6 个提示忽略 |
| 7 | 2 个文件共 20.0MB，再选第 3 个 | 拒绝（剩余 0） |
| 8 | 2 个文件共 18.0MB，第 3 个 2.0MB | 允许（剩余恰好相等） |
| 9 | 2 个文件共 18.0MB，第 3 个 2.1MB | 拒绝并提示"剩余 2.0MB" |
| 10 | 删除 1 个后立即再选 | 额度已释放，允许 |
| 11 | 上传中删除文件 | 请求中断、额度释放、无残留（尽力） |
| 12 | 上传失败的文件 | 按 D3 决策：占额度或自动移除 |
| 13 | 绕过前端直接调上传 API 传 15MB | 后端 413 |
| 14 | 绕过前端发送 6 个文件 | 后端 422 |
| 15 | 阈值接口挂了 | 前端用默认 10/5/20，不放行 |
| 16 | 配置 `enabled=false` | 前后端都不限 |
| 17 | 配置缺失 `upload_limits` 段 | 用默认值，正常启动 |

---

## 8. 改动文件清单

**后端（6 个）**
| 文件 | 改动 |
|---|---|
| `easy_agent/config/config.py` | 新增 `UploadLimitsConfig`、`_parse_upload_limits_config`、`Config.upload_limits` 字段、启动一致性自检 |
| `easy_agent/config/config-example.yaml` | 新增 `upload_limits` 段（含 nginx 提示注释） |
| `easy_agent/api/settings.py` | 新增 `GET /agent/settings/upload-limits` |
| `easy_agent/api/sessions.py` | 单文件上限校验（413）；可选改分块写 |
| `easy_agent/api/chat.py` | 发送接口兜底（个数/总量，422） |
| `easy_agent/api/files.py` | （可选，D8）资产上传单文件校验 |

**前端（各 2 个文件 × 2 套）**
| 文件 | 改动 |
|---|---|
| `frontend/src/api/settings.js`、`frontend-vue2/src/api/settings.js` | 新增 `getUploadLimits()` |
| `frontend/src/components/ChatInput.vue`、`frontend-vue2/src/components/ChatInput.vue` | 拉阈值 + `pickAcceptable()` 入列前校验 + 提示条 UI + 失败原因展示（+ 可选额度展示） |

**测试（1 个新增）**
| 文件 | 内容 |
|---|---|
| `tests/test_upload_limits.py` | 后端：单文件 413、发送 422（个数/总量）、配置默认值与缺段兼容、边界值（正好 10MB/20MB） |

---

## 9. 待你拍板的决策点

| 编号 | 问题 | 我的建议 |
|---|---|---|
| **D1** | 20MB/5 个是"输入框待发送池"还是"会话累计"？发送后是否重置？ | **池子口径；发送后重置** |
| **D2** | 「5 个」是"一次选择"还是"输入框总数"？ | **输入框内附件总数** |
| **D3** | 上传失败的文件是否占额度？ | **失败即自动移除并提示**（否则额度被死文件占住） |
| **D4** | 批量超限时部分接受还是整批拒绝？ | **按顺序部分接受 + 汇总提示** |
| **D5** | 提示形式：卡片内联红字 / 顶部提示条 / 两者 | **顶部提示条（汇总一条）** |
| **D6** | 是否常驻展示"已用 12.3MB / 20MB · 3/5 个"？ | **展示**（用户对剩余额度有预期，减少试错） |
| **D7** | 后端是否在发送接口兜底 5 个/20MB？ | **要**（否则服务端只是装饰） |
| **D8** | 资产面板上传（`/agent/files/upload`）是否套单文件 10MB？ | **套**（同一后端，口径一致更安全） |
| **D9** | 是否限制"单会话未发送文件总量"？ | **不做**（需服务端状态，收益低） |
| **D10** | 是否同时限制文件类型（`accept` 白名单）？ | 本次不做，单独提需求 |

---

## 10. 落地顺序（确认后执行）

1. 后端配置 + 下发接口（可独立验证：`curl /agent/settings/upload-limits`）；
2. 后端单文件 413 + 发送接口 422（配 `tests/test_upload_limits.py`）；
3. 前端阈值拉取 + `pickAcceptable()` 校验（先保证拦截正确，提示文案可先简单）；
4. 前端提示条 UI + 失败原因展示 + 额度展示（两套前端同步，构建验证）；
5. 手工回归第 7 章用例矩阵。

> 风险提示：本次改动会让"原本能传的大文件"被明确拒绝，属于**行为收紧**；上线前需通知测试同事，并把 `enabled=false` 作为应急开关写进使用说明。

---

# 11. 决策落地与变更范围评估（2026-10-15 确认版）

## 11.1 已确认口径

| 编号 | 问题 | 结论 |
|---|---|---|
| D1 | 20MB/5 个的口径与重置时机 | **单个会话、单次对话**（输入框待发送附件池）；发送消息后输入框清空 → 额度自动重置 |
| D2 | 「5 个」的计数范围 | **输入框附件总数**（非单次选择动作） |
| D3 | 失败文件是否占额度 | **不占**（`uploadStatus === 'error'` 不计入数量与体积）；重新上传时若"非失败文件数 + 1 > 5"则**拒绝** |
| D4 | 一批选择超过剩余名额 | **整批拒绝**，提示"最多只能上传 5 个文件"，让用户重新选择 |
| D5 | 提示形式 | 未明确 → 默认**输入框上方汇总提示条**（2.6s 自动消失、可手动关闭） |
| D6 | 常驻展示已用额度 | **做**（大小 + 数量） |
| D7 | 后端 send 接口兜底 | 未明确 → 默认**做**（前端可绕过，不做则限额无约束力） |
| D8 | 资产面板上传 | **本次不做**（缩小影响面） |
| D9 | 单会话未发送文件总量限制 | **不做** |
| D10 | 文件类型 `accept` 白名单 | **不做** |

### 额度定义（实现口径）

```
计额集合 = uploadedFiles 中 uploadStatus !== 'error' 的条目（含 uploading / completed）
usedCount = 计额集合.length
usedBytes = Σ 计额集合.size
可用名额 = maxFilesPerMessage - usedCount
可用体积 = maxTotalBytes - usedBytes
```

**注意**：失败文件不占额度 ⇒ **列表条数可能大于额度计数**（例如 5 个成功 + 1 个失败 = 列表 6 行、额度显示 5/5）。因此常驻额度展示必须写清口径，例如：

> `已用 12.3 / 20 MB · 3 / 5 个（1 个失败未计入）`

否则用户会认为"显示错了"。

### 单批选择的判定顺序（`handleFileSelect`）

1. `enabled === false` → 全部放行（应急开关）。
2. **整批拒绝检查**：`usedCount + 本批文件数 > maxFilesPerMessage` → **整批拒绝**（含"本批自身 > 5"与"已有 3 个 + 本批 3 个"两种情形，一条判断式覆盖 D4），提示"最多只能上传 5 个文件"。
3. **逐个文件检查**（保持用户选择顺序，贪心）：
   - `size > maxFileSize` → 跳过该文件并记录"单文件超限"；
   - `usedBytes + 已接受字节 + size > maxTotalBytes` → 跳过该文件并记录"超出剩余空间（剩余 X）"；
   - 通过 → 接受，累加计数与体积。
4. 接受的文件才入列、才调用 `uploadOneFile()` 并行上传；被跳过的文件**不入列、不上传**，最后汇总成一条提示。

> 第 3 步的"跳过"是粒度为**单个文件**的，只有第 2 步（数量不满足）是**整批拒绝** —— 这正是 D4 的语义。

## 11.2 精确变更清单

| 文件 | 位置 | 改动内容 | 新增 | 修改 |
|---|---|---|---|---|
| `easy_agent/config/config.py` | 新增 `UploadLimitsConfig` 类 | 4 字段（含默认值）+ 注释 | ~14 行 | 0 |
| ↑ | `Config` 类（`class Config`，L330+） | 新增字段 `upload_limits: UploadLimitsConfig = Field(default_factory=...)` | 2 行 | 0 |
| ↑ | 新增 `_parse_upload_limits_config` | 解析 + 一致性自检（`total >= file_size`、均 >0，异常仅告警） | ~14 行 | 0 |
| ↑ | `from_yaml`（L479 `return cls(...)`） | 增加 `upload_limits=...` 实参 | 1 行 | 0 |
| `easy_agent/config/config-example.yaml` | 文件末尾 | 新增 `upload_limits` 段 + nginx 提示注释 | ~12 行 | 0 |
| `easy_agent/config/config.yaml`（本地实配，gitignore） | 文件末尾 | 同步加同一段（便于现场调参；不加也有默认值） | ~8 行 | 0 |
| `easy_agent/api/settings.py` | 文件末尾（仿 `/web-search`） | 新增 `GET /agent/settings/upload-limits`（公共、无鉴权） | ~22 行 | 0 |
| `easy_agent/api/sessions.py` | `upload_session_file` L379 `content = await file.read()` 之后 | 单文件 >10MB → `HTTPException(413, detail={code,message,limit_mb,actual_mb})`，**不落盘** | ~14 行 | 0（纯插入） |
| `easy_agent/api/chat.py` | 导入区 | 增加 `get_agent_config` 到 `from ..services import (...)` | 1 行 | 0 |
| ↑ | `chat_stream` L296 `if not request.message` 之后（**会话创建/鉴权之前**，保证拒绝无副作用） | `files` 个数/总量兜底 → `422`；resume 接口无 files，不动 | ~18 行 | 0（纯插入） |
| `frontend/src/api/settings.js`、`frontend-vue2/src/api/settings.js` | 文件末尾 | `getUploadLimits()` | 各 ~8 行 | 0 |
| `frontend/src/components/ChatInput.vue`、`frontend-vue2/src/components/ChatInput.vue` | import + state | `DEFAULT_LIMITS`、`uploadLimits`、`loadUploadLimits()`、`onMounted` 调用 | 各 ~18 行 | 0 |
| ↑ | 新增 `pickAcceptable()` | 11.1 的判定四步（含失败不占额度、整批拒绝、逐个跳过、原因汇总） | 各 ~32 行 | 0 |
| ↑ | `handleFileSelect`（现有 L1475 附近） | 用 `pickAcceptable` 包住现有循环：`accepted` 才入列上传 | — | 各 ~8 行 |
| ↑ | 模板文件列表区 | 常驻额度行（大小 + 数量 + 失败未计入说明） | 各 ~6 行 | 0 |
| ↑ | computed | `usedBytes/usedCount/limitsText` | 各 ~10 行 | 0 |
| ↑ | 模板 + 逻辑 | 汇总提示条（state + 2.6s 自动消失 + 关闭） | 各 ~18 行 | 0 |
| ↑ | 样式区 | 额度行 + 提示条 + 暗色覆盖（纯 `var()` + 字面量回退） | 各 ~45 行 | 0 |
| `tests/test_upload_limits.py` | 新文件 | 见 11.5 | ~120 行 | 0 |

**总量估算**：新增 ≈ **465 行**（后端 ≈ 95、前端两套 ≈ 250、测试 ≈ 120），**修改现有代码 ≈ 23 行**（前端两处 `handleFileSelect` 各 8 行 + 其余为单行插入）。**无删除、无重构、无 DB 变更、无接口签名变更。**

## 11.3 "尽量不影响现有功能"的具体保证

1. **纯加法**：新配置段用 `default_factory` → **老 `config.yaml` 不加这段也能正常启动**；改 `enabled=false` 一键回到现状。
2. **正常请求不走新代码**：≤10MB 的上传、≤5 个/≤20MB 的发送，判定后原样执行既有流程，对性能与行为零影响。
3. **前端上传机制不动**：并行上传、进度条、`cancelled` 标记、`abort` 中断、右上角 × 角标、失败过滤（`send()` 只发 completed）全部保留，只是**在"入列"之前加一道筛选**。
4. **消息气泡不动**：`ChatMessage.vue` 无需改动。
5. **范围外不动**：资产面板上传（`api/files.py`）、知识库上传（`knowledges/`）、`file_parser` 50MB 解析上限均不碰。
6. **新增接口不影响既有接口**：`/agent/settings/upload-limits` 是全新公共接口，不改任何既有路径/响应结构。
7. **`send()` 里"有文件上传中则不发送"的既有保护保留**，与新校验无冲突。

## 11.4 唯一的预期行为变化（需写入测试说明）

| 场景 | 现在 | 改后 |
|---|---|---|
| 单文件 > 10MB | 尝试上传（可能失败） | 前端直接拒绝 + 后端 413（不再尝试） |
| 输入框附件 > 5 个 | 允许 | 拒绝（整批） |
| 附件合计 > 20MB | 允许 | 拒绝 |
| >10MB 文件上传（历史遗留行为） | 卡片显示"上传失败" | 根本不会发起上传，改为提示条说明原因 |

这是**行为收紧**，属预期；应急开关 `upload_limits.enabled: false`。

## 11.5 测试范围

`tests/test_upload_limits.py`（新增，pytest）：
- 配置：无 `upload_limits` 段 → 默认 10/5/20；`enabled=false` → 校验全部跳过；`max_total < max_file_size` → 触发告警但不崩。
- 接口：`GET /agent/settings/upload-limits` 返回 4 字段。
- 上传：11MB → 413 且**未落盘**；9.9MB → 200；正好 10MB → 200（`>` 判定）。
- 发送：6 个文件 → 422；3×7MB（合计 21MB）→ 422；5 个 × 4MB（合计 20MB）→ 通过。

手工用例矩阵沿用第 7 章表格（其中 #12「失败文件」按新口径改为"失败不占额度、可继续选新文件"，#15「阈值接口挂」按默认值兜底）。

## 11.6 仍需你一句话确认的 3 项（已给默认值，不确认则按默认实现）

| 编号 | 待确认 | 默认实现 |
|---|---|---|
| **B1**（D5） | 提示形式 | 输入框上方的**汇总提示条**，2.6s 自动消失、可关闭（与 ExpertListPanel 的 toast 风格一致） |
| **B2**（D4 补充） | 批内**单个文件**超 10MB 或加进来会超 20MB 时：整批拒绝还是只跳过该文件？ | **只跳过该文件**，其余照常上传；提示中逐个列出被跳过的文件名与原因（"整批拒绝"仅用于数量不满足） |
| **B3**（D7） | 后端 `send` 接口是否兜底 5 个/20MB | **做**（~18 行；不做则前端可绕过） |
| **B4** | 是否在失败卡片上加「重试」按钮 | **不加**（用户点 × 删掉失败项后重新选择即可，走同一套校验；D3 的"重新上传拦截"由该校验自然满足） |

---

# 12. 实施记录（2026-10-15）

## 12.1 最终口径（覆盖第 11.6 章）

| 编号 | 用户最终选择 |
|---|---|
| B1 | 沿用默认：输入框上方**汇总提示条**，2.6s 自动消失、可关闭 |
| B2 | **跳过文件**，并明确告知"超过大小限制"（逐个列出文件名 + 原因） |
| B3 | **不做**后端 send 接口兜底（担心 `chat` 接口有其他调用方） |
| B4 | **失败卡片加重试按钮**；**单文件超限的不加重试**（重试也没用） |

## 12.2 实际改动

| 文件 | 改动 | 行数 |
|---|---|---|
| `easy_agent/config.py` | 新增 `UploadLimitsConfig`、`Config.upload_limits`（`default_factory`）、`_parse_upload_limits_config`（含"总量 < 单文件"告警）、`from_yaml` 接入 | +54 |
| `easy_agent/config/config-example.yaml` | 新增 `upload_limits` 段 + nginx 提示注释 | +13 |
| `easy_agent/config/config.yaml`（本地实配） | 同步新增 `upload_limits` 段 | +13 |
| `easy_agent/api/settings.py` | 新增 `GET /agent/settings/upload-limits`（公共接口） | +33 |
| `easy_agent/api/sessions.py` | 新增 `_get_max_file_size_mb()`；`upload_session_file` 在 `read()` 之后校验单文件上限 → `413`（结构化 detail，**不落盘**） | +41 |
| `frontend/src/api/settings.js`、`frontend-vue2/src/api/settings.js` | 新增 `getUploadLimits()` | +10 / +11 |
| `frontend/src/api/files.js`、`frontend-vue2/src/api/files.js` | 把后端结构化 `detail`（对象）归一成带 `code` 的 `Error`，供卡片展示原因 + 判断是否可重试 | +13/-2 / +30/-12 |
| `frontend/src/components/ChatInput.vue` | 限额拉取与默认兜底、`pickAcceptable()` 入列前校验、汇总提示条、常驻额度行、失败原因 + 「重试」按钮、CSS（IE/旧 Edge 兼容） | +339/-38 |
| `frontend-vue2/src/components/ChatInput.vue` | 同上（Options API 实现） | +346/-38 |
| `tests/test_upload_limits.py` | 新增 8 个用例 | +190 |

**合计 +890 / -90**（其中"删除"多为被替换的旧实现行）。**未做**：`chat.py` 的 send 兜底（B3）、资产面板上传限制（D8）、单会话累计限制（D9）、`accept` 类型白名单（D10）。

## 12.3 判定逻辑（与第 11.1 章一致）

- 计额集合 = `uploadStatus !== 'error'`（失败不占额度）→ 额度行会额外标注"（N 个失败未计入）"。
- `usedCount + 本批数 > maxFiles` → **整批拒绝**（提示"最多只能上传 5 个文件（已用 N 个），请重新选择"）。
- 逐个检查：`size > 10MB` → 跳过并提示"超过单文件上限 10MB"；`已用 + size > 20MB` → 跳过并提示"超出剩余空间 X，总上限 20MB"。
- 「重试」：复用同一个 `File` 对象重跑上传，重试前重算额度（满 5 个 / 超出剩余空间 / 单文件超限 → 拒绝并提示）；后端返回 `UPLOAD_FILE_TOO_LARGE` 或消息含"不能超过 N MB"时**不渲染**重试按钮。

## 12.4 验证

- 后端：`pytest tests/test_upload_limits.py -v` → **8 passed**（含真实 `10MB+1 字节 → 413 且未落盘`、边界"正好 10MB 放行"、`enabled=false` 放行、配置缺段回落默认、写歪告警）。
- 回归：`pytest tests/api/test_sessions.py tests/api/test_settings.py tests/config -q` → **31 passed**。
- 前端：`vite build --outDir dist-verify-limits2` ✓、`vue-cli-service build --mode win --dest dist-verify-limits2-vue2` ✓（两套模板与脚本均编译通过）。

## 12.5 已知说明

1. 后端 `send` 接口未做兜底（B3）：绕过前端直接调 API 仍可发送 >5 个或 >20MB 的附件清单，但**单文件 10MB 仍被 upload 接口挡住**（413）。若后续确认 `chat` 接口无其他调用方，可按第 5.2 节补上（约 19 行）。
2. 额度口径为"前端池子"，多标签页/多会话各自的输入框互相独立；发送消息后额度重置。
3. `frontend-vue2/src/api/files.js` 当前未设置 `timeout: 0`（上传请求沿用全局 30s 超时）。10MB 上限下通常足够；若大文件+弱网超时，可在该请求上加 `timeout: 0`。
4. 常驻额度行位于文件列表容器内，该容器在 vue2 侧有 `max-height: 204px` 滚动（文件多时额度行会随内容一起滚动）。


