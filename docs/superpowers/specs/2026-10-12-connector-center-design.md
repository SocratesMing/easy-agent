# 连接器中心（专家 · 技能 · 连接器）设计

- 日期：2026-10-12
- 状态：待评审
- 范围：技能中心改造成三 Tab 容器；MCP 能力从设置弹窗整体迁入「连接器」；后端 MCP 接口迁移到 `/agent/connectors`；新增内置连接器目录
- 选定方案：
  - 入口：**技能中心（`SkillCenter.vue`）顶部三 Tab**，不复用设置弹窗左栏
  - 连接器数据源：**后端内置目录**（`easy_agent/connectors/catalog.json`，本期为空）
  - 接口归属：**MCP 接口整体迁到 `/agent/connectors/mcp*`**，`/agent/settings/mcp*` 全删
  - 旧「公共市场」（全局 mcp.json）：**一并删除**（`GET /mcp/market`、`POST /mcp/market/add` 及前端代码）
  - 移除入口：**卡片详情弹层里给「移除」**
  - 前端范围：`frontend/`（Vue3）与 `frontend-vue2/`（Vue2.7）两份同步

## 背景

设置弹窗（`frontend/src/components/SettingsPanel.vue`，Vue3 版 1809 行）左栏为「记忆 / 提示词 / MCP」，MCP 页签内含：服务列表 + 启停开关 + 保存配置 + 预览 mcp.json + 添加（手动粘 JSON / 从公共市场添加）+ 删除。

技能中心（`frontend/src/components/SkillCenter.vue`，1106 行）是独立全屏面板，由左侧栏 `SessionList.vue` 的「技能中心」按钮打开（`showSkillCenter`），内部只有一组 Tab「公共技能 / 我的技能」。

后端 `easy_agent/api/settings.py`（684 行）里 MCP 段约 330 行：`GET/PUT /agent/settings/mcp`、`GET /agent/settings/mcp/market`、`POST /agent/settings/mcp/market/add`、`POST /agent/settings/mcp/server`、`DELETE /agent/settings/mcp/server/{name}`，依赖 `services/mcp.py`（`load_mcp_config` / `get_mcp_tools` / `invalidate_mcp_cache` / `validate_mcp_servers` / `normalize_servers_mapping` / `_unpack_error`）。

参考形态取自 CodeBuddy IDE 的连接器页：顶部「专家 · 技能 · 连接器」三 Tab → 连接器页为「搜索框 + 自定义连接器按钮 + 卡片网格 + 卡片右上加号」；点「自定义连接器」进「MCP 服务管理」整页（配置文件路径条 + 带行号 JSON 编辑器 + 取消/保存 + 返回 MCP 列表 + 添加 MCP）；点「添加 MCP」进整页粘贴 JSON。

## 现状问题

1. **入口错位**：MCP 是「对外工具接入」，却挂在「个人设置」里，与技能中心（同为能力接入）割裂，用户要分别记两个入口。
2. **单个组件过载**：MCP 的配置、市场、增删、预览全挤在 `SettingsPanel.vue`，该文件已达 1809 行；`SkillCenter.vue` 也要再挂两个 Tab。
3. **市场概念冗余**：全局 `mcp.json` 当「公共市场」用，实际只有 3 条示例配置，且用户真正需要的「一键接入某个已知服务」没有承载处。
4. **无法直接编辑配置文件**：用户想看/改最终 `mcp.json` 只能通过「预览」（只读）+ 逐条增删。

## 目标与验收

- 侧边栏「技能中心」入口打开的面板顶部为三个 Tab：专家 / 技能 / 连接器；技能 Tab 行为与改造前完全一致（公共技能 / 我的技能 / 导入 / 添加 / 移除 / 下载）。
- 设置弹窗只剩「记忆 / 提示词」，MCP 相关状态、函数、样式全部移除。
- 连接器 Tab 三个视图可互相跳转：
  - 列表视图：搜索框过滤 + 「自定义连接器」按钮 + 卡片网格；库存为空时显示空态引导。
  - 点卡片 → 弹层展示**名称、描述、对应 mcp.json 片段（可复制）**；已安装的卡片在弹层里提供「移除」。
  - 「自定义连接器」→ MCP 服务管理视图：标题「MCP 服务管理」+ 副标题 + 「+ 添加 MCP」+ 关闭；「← 返回 MCP 列表」+ 取消/保存；配置文件路径条 + 行号 JSON 编辑器（可编辑、JSON 报错就地提示）。
  - 「+ 添加 MCP」→ 添加视图：整页粘贴 JSON（支持 `servers` / `mcpServers` / 对象 / 单条四种格式）+ 格式示例 + 确认添加，成功后回列表并刷新。
- 列表视图既显示内置目录项，也显示用户 `mcp.json` 里存在但不在目录中的**自定义项**（标记「自定义」），可点开看配置、可移除。
- 后端：`/agent/settings/mcp*`、`/agent/settings/mcp/market*` 路径不再存在；`/agent/connectors*` 提供上述全部能力；`mcp_api_keys` 表与 `services/mcp.py` 不动。
- 两份前端 `npm run build` 通过，且完成真机渲染验收（截图 + computedStyle 断言 + 明暗主题 + 关键交互）。

## 非目标

- 不做「专家」Tab 内容：本期仅空态占位，文案「专家能力建设中，敬请期待」（可随时改）。
- 不预置第三方 SaaS 连接器目录（`catalog.json` 本期为空数组，只落结构与加载链路）。
- 不做连接器的启用/停用开关（取代方案：在 JSON 里增删该条目）。
- 不做 `mcp.json` 的服务端共享/多用户市场。
- 不改 `services/mcp.py` 的 transport 解析与工具加载链路。
- 不改 `mcp-server/` 独立子项目。

## 设计

### ① 面板结构（`SkillCenter.vue` 变容器）

```
SkillCenter.vue（容器：header + 三 Tab + 连接器视图状态机）
├── 专家     → 空态占位（容器内联，不单独建文件）
├── 技能     → SkillListPanel.vue      （现有技能内容整体平移）
└── 连接器   → ConnectorListPanel.vue  （列表 + 详情弹层）
             ├── McpConfigEditorPanel.vue （管理视图，替换整块内容）
             └── McpAddPanel.vue          （添加视图，替换整块内容）
```

- Tab 状态：`activeTab: 'experts' | 'skills' | 'connectors'`。
- 连接器视图状态：`connectorView: 'list' | 'manage' | 'add'`，放在容器里，由子组件 `emit` 切换（`ConnectorListPanel` emit `manage`/`add`，编辑器与添加页 emit `back`）。
- 「技能」Tab 原有交互（公共/我的、导入 zip、添加、移除、下载、加载态、错误重试）原样保留，不重构其内部逻辑，只搬家。
- 侧边栏按钮文案保持「技能中心」不变（入口含义未变，只改面板内部结构）。

### ② 内置连接器目录

数据文件 `easy_agent/connectors/catalog.json`，本期落空结构：

```json
{
  "connectors": []
}
```

单条格式（`id` 即写入用户 `mcp.json` 的 server 名，保证 install/installed/移除三处一一对应）：

```json
{
  "id": "market-data",
  "name": "行情数据",
  "description": "通过自建 market MCP 查询行情与持仓",
  "icon": "chart",
  "config": {
    "transport": "streamable_http",
    "url": "http://127.0.0.1:8100/mcp/market/",
    "headers": { "Authorization": "Bearer <API Key>" }
  }
}
```

加载器 `easy_agent/services/connectors.py`：

- `load_catalog(path: str | None = None) -> list[dict]`：读文件 → 校验必填字段（`id` / `name` / `config`，`id` 需为安全标识符）→ 跳过非法项并记 warning；文件缺失或 JSON 非法时返回空列表（不抛 500）。
- `catalog_connectors(username) -> dict`：组装 `{connectors: [...], custom: [...], path}`，其中 `connectors` 每项带 `installed`（`id` 是否已在用户 `mcp.json` 的 servers 中），`custom` 为「用户 mcp.json 里存在但不在目录中」的服务（`name` + 归一化后的 `config`）。

### ③ 后端接口（新路由 `easy_agent/api/connectors.py`，prefix `/agent/connectors`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/agent/connectors` | 目录 + 自定义项 + `path`（用户 mcp.json 路径） |
| POST | `/agent/connectors/install` | body `{id}`：目录取配置写入用户 mcp.json；目录无此项 404；已存在 409；成功后失效 MCP 缓存与用户 Agent，并校验该 server 返回 `server_status` |
| POST | `/agent/connectors/uninstall` | body `{id}`：从用户 mcp.json 删除；不存在 404；成功后失效缓存与 Agent |
| POST | `/agent/connectors/mcp` | body `{config}`：粘贴 JSON 添加（沿用现有四种格式解析 `_extract_servers`），判重 409，未识别格式 400；成功后失效缓存与 Agent，并校验新增项 |
| GET | `/agent/connectors/mcp/raw` | `{path, content}`：用户 mcp.json 原文；文件不存在时返回 `{"servers": {}}` |
| PUT | `/agent/connectors/mcp/raw` | body `{content}`：JSON 解析失败 400（返回错误原因）→ 归一化（`servers` / `mcpServers` / 裸对象 / 单条）→ 写盘 → 失效缓存与 Agent → 返回 `{path, servers, server_status}` |
| DELETE | `/agent/connectors/mcp/{name}` | 删除单个 server（弹层「移除」） |

迁移与删除：

- `easy_agent/api/settings.py` 删除整个 MCP 段（`_market_mysql_config`、`GET/PUT /mcp`、`/mcp/market*`、`/mcp/server*` 及 `_read_user_mcp_raw` / `_merge_mcp_env` / `_write_user_mcp_raw` / `_extract_servers` 等辅助函数），这些实现**平移**到 `connectors.py` 复用，行为不变。
- `PUT /agent/connectors/mcp`（原「按 servers 全量覆盖」）**不再保留**：原先只服务于设置页的启停开关 + 保存配置，新 UI 用原文编辑器取代。**代价**：不支持「保留配置但临时停用」，停用需在 JSON 里删除该条目。
- 旧「公共市场」相关：`GET /agent/settings/mcp/market`、`POST /agent/settings/mcp/market/add` 删除；前端 `utils/mcpMarket.js` 与其测试一并删除。
- `easy_agent/app.py` 注册 `connectors_router`；`easy_agent/api/__init__.py` 导出。

### ④ 前端接口层

新增 `src/api/connectors.js`（两份前端各一份，Vue2 用现有 `request` 封装）：

```
getConnectors()            GET    /agent/connectors
installConnector(id)       POST   /agent/connectors/install
uninstallConnector(id)     POST   /agent/connectors/uninstall
addMcpConfig(config)       POST   /agent/connectors/mcp
getMcpRaw()                GET    /agent/connectors/mcp/raw
saveMcpRaw(content)        PUT    /agent/connectors/mcp/raw
deleteMcpServer(name)      DELETE /agent/connectors/mcp/{name}
```

`src/api/settings.js` 只保留记忆 / 提示词 / 模型 / 联网搜索，MCP 相关函数全部删除。

### ⑤ JSON 编辑器

用 **Monaco**（两份前端都已有 `monaco-editor` 依赖，`CodePreview.vue` 已在用；Vue3 由 `vite-plugin-monaco-editor` 提供 worker）：

- 语言 `json`，左侧行号、语法高亮、`minimap` 关闭、`automaticLayout: true`。
- 顶部路径条展示后端返回的 `path`（只读，不可点击）。
- 「保存」把编辑器内容 PUT 上去；后端 400 时把错误信息显示在编辑器下方；保存成功后若 `server_status` 里有失败项，提示「以下服务连接异常：xxx」。
- 初始化异常（Monaco 起不来）时降级为原生 `textarea`，保证功能不丢。

### ⑥ 错误处理与状态

- 目录文件缺失/非法 → 目录为空 + 后端 warning 日志，前端显示空态（不报错弹窗）。
- install 已存在 → 前端把该卡片置为「已添加」并提示；404/409/400 均取后端 `detail` 文案展示。
- 保存原文 JSON 非法 → 编辑器下方红色提示，不写盘。
- 保存/添加后 server 校验失败 → 数据已落盘（与现有行为一致），前端显式提示失败项名称。
- 所有写操作后刷新列表（重新 GET `/agent/connectors`）。

### ⑦ 交付文件清单

**后端**

| 动作 | 文件 |
|---|---|
| 新增 | `easy_agent/connectors/catalog.json`（空目录结构） |
| 新增 | `easy_agent/services/connectors.py` |
| 新增 | `easy_agent/api/connectors.py` |
| 修改 | `easy_agent/api/settings.py`（删 MCP 段）、`easy_agent/api/__init__.py`、`easy_agent/app.py` |
| 新增测试 | `tests/api/test_connectors.py` |
| 修改测试 | `tests/api/test_settings.py`（迁走 MCP 用例）、删除 market 相关用例 |

**前端（`frontend/` 与 `frontend-vue2/` 各一套）**

| 动作 | 文件 |
|---|---|
| 改造 | `src/components/SkillCenter.vue`（容器 + 三 Tab） |
| 抽取 | `src/components/SkillListPanel.vue`（原技能内容） |
| 新增 | `src/components/ConnectorListPanel.vue`、`McpConfigEditorPanel.vue`、`McpAddPanel.vue`、`src/api/connectors.js` |
| 修改 | `src/components/SettingsPanel.vue`（删 MCP）、`src/api/settings.js`（删 MCP 接口） |
| 删除 | `src/utils/mcpMarket.js`、`src/utils/mcpMarket.test.js` |

## 验收方式

1. 后端：`pytest tests -q`（新增 `test_connectors.py` 覆盖：空目录、有目录项、install 写入与 409、uninstall 404、raw GET/PUT 往返、非法 JSON 400、自定义项出现在 `custom`、写操作后缓存失效）。
2. 前端：两份 `npm run build`。
3. 真机渲染验收（按项目既有约定，构建通过不算验收）：临时起后端 + 前端，覆盖
   - 三 Tab 切换与技能 Tab 原有功能不回归
   - 连接器列表空态、搜索框输入、卡片点击弹层（临时往 `catalog.json` 塞 1 条验证后删除）
   - 「自定义连接器」→ 编辑 `mcp.json` → 保存 → 返回，路径与内容正确
   - 「+ 添加 MCP」粘贴 JSON 添加成功、重复添加报 409
   - 明暗两种主题下的关键样式（卡片圆角/边框/对比度、弹层、编辑器配色）
   - 截图存档

## 风险与取舍

- **能力取舍**：去掉「启停开关 + 全量保存（`PUT /agent/connectors/mcp`）」，改为直接编 JSON。若需要「保留配置但停用」，后续再补。
- **文件体积**：`SkillCenter.vue` 若不拆分，改造后会突破 2000 行，故按 ① 的方式拆成容器 + 3 个面板。
- **两份前端同步**：Vue3 用 `<script setup>`、Vue2 用 Options API，逻辑一致但代码不共享，需分别维护（沿用现状）。
- **Monaco 体积**：编辑器会增大前端包，但依赖已存在（`CodePreview.vue` 已加载），不新增依赖。
