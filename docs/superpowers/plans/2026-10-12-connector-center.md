# 连接器中心实施计划

> 设计文档：`docs/superpowers/specs/2026-10-12-connector-center-design.md`（已确认）

**目标：** 技能中心改为「专家 / 技能 / 连接器」三 Tab；MCP 能力从设置弹窗整体迁入「连接器」，后端接口从 `/agent/settings/mcp*` 迁到 `/agent/connectors*`，并新增内置连接器目录（本期为空）。

**技术栈：** FastAPI + pytest（后端）；Vue 3 `<script setup>`（`frontend/`）与 Vue 2.7 Options API（`frontend-vue2/`）双份前端；Monaco 作 JSON 编辑器。

---

### Task 1: 内置连接器目录与加载器

**Files:**
- Create: `easy_agent/connectors/catalog.json`
- Create: `easy_agent/services/connectors.py`
- Test: `tests/api/test_connectors.py`（目录加载部分）

- [x] 落 `catalog.json` 空结构 `{"connectors": []}`
- [x] `load_catalog(path=None)`：读文件 → 校验 `id`/`name`/`config`，非法项跳过 + warning；文件缺失/JSON 非法返回 `[]`
- [x] `catalog_connectors(username)`：返回 `{connectors:[{id,name,description,icon,config,installed}], custom:[{name,config}], path}`
- [x] 单测：空目录、含 1 条合法 + 1 条非法项、用户已安装标记、custom 收集

### Task 2: 后端接口迁移到 /agent/connectors

**Files:**
- Create: `easy_agent/api/connectors.py`
- Modify: `easy_agent/api/settings.py`（删 MCP 段）
- Modify: `easy_agent/api/__init__.py`、`easy_agent/app.py`
- Test: `tests/api/test_connectors.py`

- [x] 平移到 `connectors.py` 并复用：`_read_user_mcp_raw` / `_write_user_mcp_raw` / `_merge_mcp_env`（不再需要时删除）/ `_extract_servers`
- [x] `GET /agent/connectors`、`POST /agent/connectors/install`、`POST /agent/connectors/uninstall`
- [x] `GET /agent/connectors/mcp/raw`、`PUT /agent/connectors/mcp/raw`
- [x] `POST /agent/connectors/mcp`、`DELETE /agent/connectors/mcp/{name}`
- [x] `settings.py` 删除 MCP 段与该模块文档串里的 MCP 提法；`app.py` 注册新路由
- [x] 删旧「公共市场」接口与前端 `utils/mcpMarket.js`、`utils/mcpMarket.test.js`
- [x] 测试：install/409、uninstall/404、raw GET/PUT 往返、非法 JSON 400、新增 409、自定义项出现在 `custom`、路径已迁（`/agent/settings/mcp*` 不存在）
- [x] `pytest tests -q`（除既有失败外全绿）

### Task 3: 技能中心容器 + 技能内容平移（Vue3）

**Files:**
- Modify: `frontend/src/components/SkillCenter.vue`
- Create: `frontend/src/components/SkillListPanel.vue`

- [x] 抽出原技能内容为 `SkillListPanel.vue`（公共/我的、导入、添加、移除、下载逻辑原样搬家）
- [x] `SkillCenter.vue` 变为容器：三 Tab（专家/技能/连接器）即面板首行（无标题栏）+ 连接器视图状态机；关闭按钮置于 Tab 行右侧
- [x] 专家 Tab 空态：「专家能力建设中，敬请期待」
- [x] `npm run build` 通过；技能 Tab 行为不回归

### Task 4: 连接器面板（Vue3）

**Files:**
- Create: `frontend/src/api/connectors.js`
- Create: `frontend/src/components/ConnectorListPanel.vue`
- Create: `frontend/src/components/McpConfigEditorPanel.vue`
- Create: `frontend/src/components/McpAddPanel.vue`

- [x] `api/connectors.js`：7 个接口封装
- [x] 列表视图：搜索框、自定义连接器按钮、卡片网格（图标/名称/描述/+）、空态、`custom` 卡片带「自定义」标记
- [x] 卡片弹层：名称、描述、mcp.json 片段（复制）、已安装项给「移除」
- [x] 管理视图：路径条 + Monaco JSON 编辑器 + 取消/保存 + 「+ 添加 MCP」+ 关闭；Monaco 起不来时降级 textarea
- [x] 添加视图：整页粘 JSON + 格式示例 + 确认添加（409 就地提示）
- [x] 全部写操作后重新拉列表

### Task 5: 设置弹窗剥离 MCP（Vue3）

**Files:**
- Modify: `frontend/src/components/SettingsPanel.vue`
- Modify: `frontend/src/api/settings.js`

- [x] 删 MCP Tab、状态、函数、样式（含深色主题规则）
- [x] `settings.js` 删 MCP 相关函数

### Task 6: Vue2 前端同步（Task 3-5 的 Options API 版本）

**Files:**
- Modify/Create: `frontend-vue2/src/components/{SkillCenter,SkillListPanel,ConnectorListPanel,McpConfigEditorPanel,McpAddPanel,SettingsPanel}.vue`
- Create: `frontend-vue2/src/api/connectors.js`
- Modify: `frontend-vue2/src/api/settings.js`
- Delete: `frontend-vue2/src/utils/mcpMarket.js`、`mcpMarket.test.js`

- [x] 逻辑与 Vue3 版一一对应（`data`/`computed`/`methods` 形式）
- [x] `npm run build` 通过

### Task 7: 文档、验收与提交

**Files:**
- Modify: `README.md`、`mcp-server/README.md`

- [x] README 更新：MCP 入口在「技能中心 → 连接器」，接口路径 `/agent/connectors*`
- [x] `pytest tests -q`
- [x] 两份 `npm run build`
- [x] 真机渲染验收：三 Tab 切换、连接器空态、临时塞 1 条目录项验证卡片+弹层+装/卸、编辑器保存、添加 MCP 409、明暗主题（截图 + computedStyle 断言），验收后移除临时目录项
- [x] 提交并推送
