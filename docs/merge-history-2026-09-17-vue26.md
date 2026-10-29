# Vue 2 分支合并历史与适配记录

日期：2026-09-17。目标分支：`feature_sunyicong`。本次仅在本地合并，没有 push，也没有创建 PR。

## 分支与历史

远程没有名为 `vue26` 的分支；实际拉取并合并的是开发者仓库 `origin/feat/frontend-vue2-vue26`。

| 节点 | 提交/分支 | 含义 |
| --- | --- | --- |
| 共同祖先 | `f5f3745` | feat(auth): 多进程会话与配置本地化 |
| 之前的一次上游合并 | `5c5b978` | Merge remote-tracking branch 'origin/master' |
| 之前的回退 | `7e013cb` | Revert "Merge remote-tracking branch 'origin/master'" |
| 本次合并前的知识库版本 | `daa4144` | feat(knowledge): add knowledge engineering and personnel management |
| 本次合并的上游版本 | `d03ca3f` | 会话历史加载骨架屏，避免闪空欢迎页 |
| 合并前备份 | `backup/feature-sunyicong-before-vue26-merge` | 指向 `daa4144`，保留合并前全部已提交代码 |

上游相对共同祖先有 43 个提交（含 merge/revert）。此前的 merge 和 revert 保留在历史中，本次使用双父提交合并，不改写历史。完成后可用 `git log -1 --format='%h %p %s'` 查看本次合并提交及两个父提交。

## 合并策略和实际运行目录

- 开发者的 Vue 2 源码以 `frontend-vue2/` 为准，该目录保留上游版本。
- 项目现有启动脚本和后端静态文件目录仍使用 `frontend/`。将上游 Vue 2 主界面、流式渲染、工作区和请求封装同步到这个目录，再接入知识库。
- 独立模块继续位于 `easy_agent/knowledge/`、`easy_agent/personnel/` 和 `frontend/src/features/`。保留原文双存储、文件夹、Excel 预览、导入进度、团队授权、知识选择和知识引用。
- 知识库仍从侧栏进入；“人员与权限”入口仍在知识库右上角。保留 admin 刷新登录态、会话侧栏拖拽宽度，工作区使用上游拖拽实现。
- 注意：虽然上游分支和提交名提到 Vue 2.6，两个 package.json 的实际 Vue 依赖均为 **2.7.16**；知识库组件仍使用 Vue 2.7 的 Composition API，不能据此宣称已适配纯 Vue 2.6。
- Markdown 依赖按上游 Vue 2 版本锁定 `marked@4.3.0`，避免使用 17.x 搭配旧 Renderer 签名造成运行错误。

## 必要适配

| 变化 | 处理 |
| --- | --- |
| 宿主接口迁移到 `/agent` | 前端普通聊天使用新路径；知识库/人员仍使用 `/api/knowledge`、`/api/personnel`。独立 `legacy_routes.py` 为旧宿主路径提供兼容，复用同一鉴权 |
| 上游流式函数参数变化 | `knowledge/host_streaming.py` 接收知识上下文、首批引用事件和引用元数据，调用上游执行器；引用写入后才发送完成事件 |
| 知识库右侧问答 | 继续复用公开聊天接口；保留无工具执行器，禁止通过通用 Agent 内置文件/命令工具越权，继续过滤模型内部推理 |
| 用户表重构 | `personnel/user_fields.py` 增量补齐部门、展示名、停用状态和来源等字段；保留上游工号唯一性和共享数据库活跃时间 |
| 安全策略 | 保留停用账号拒绝登录、原子 token 撤销、admin 禁止默认密码重置，以及生产管理员初始化密码要求 |
| 新增门户免密入口 | `personnel.passwordless_login_enabled` 默认 false，避免普通知识库账号仅凭用户名被冒用；受控门户部署需要显式启用并限制入口 |
| 自助注册 | 继续由 `personnel.self_registration_enabled` 控制，默认关闭；登录页面通过人员模块公开能力接口读取是否显示注册入口 |
| 配置文件迁移 | `EASY_CONFIG` 优先；上游 `config.yaml` 存在则使用它，否则兼容现有环境 YAML；开发启动明确使用现有 `config.dev.yaml` |
| 模型上下文参数改名 | 读取旧 `max_input_tokens` 为 `context_length`，不改写现有 YAML |
| 知识库表和唯一索引 | 接回模块自有 schema 初始化；生产继续要求显式迁移，保持索引失败时阻止不完整初始化 |

没有对业务数据库或原文目录执行清空、重建或回填。现有环境配置文件内容保留。生产启动可用 `EASY_CONFIG` 显式指定配置；此轮没有执行生产迁移。

## 验证结果

- 后端 **319 项通过**：API、数据库、配置、服务、初始化、知识库、人员与权限，以及多 worker 登录测试。
- 前端 **27 项通过**：Vue 2 语法、人员入口、团队权限、上传进度、Excel 预览和登录态等现有单元测试。
- `NODE_ENV=production npm run build` 成功，无缺失导出/编译错误；仍有大包体积警告。
- 增补 `tests/knowledge/test_vue26_merge.py`：上下文传递、SSE 引用顺序与历史持久化、人员字段保存、旧配置兼容、默认禁用免密冒用、新旧接口共享鉴权。
- 本地配置路径读取正确，旧模型上下文参数保持为 200000。该检查使用占位密钥，仅验证配置解析，没有调用模型。
- 没有重新启动现有服务，没有对真实 MySQL、RAGFlow 或付费模型进行端到端联调；这些不计入“已通过”结果。

复现命令（仓库根目录）：

```bash
.venv/bin/python -m pytest tests/api tests/db tests/config tests/services tests/initialization tests/knowledge tests/test_personnel.py tests/integration/test_multi_worker_auth.py -q
cd frontend
npm run test:unit
NODE_ENV=production npm run build
```

## 上游完整提交序列

按共同祖先之后的 Git 顺序列出：

```text
414476e refactor(api): 接口路径前缀统一为 /agent 并新增 Market MCP 子项目
a4b0f5c docs(mcp): MCP Server 独立 uv 子项目设计
239dd0f feat(mcp): MCP 服务拆分为独立 uv 子项目 mcp-server
8027596 feat(auth): 免密登录（用户名+用户ID 直登）并默认关闭空闲登出
9091087 refactor(bloom): 移除彭博业务并恢复配置 YAML 环境变量占位符
3d89c7c refactor(forex): 移除外汇业务接口与 BOND_BOT 现券机器人
2b169aa feat(frontend): 新增 Vue 2 版本前端项目 frontend-vue2
6bbef04 refactor(frontend-vue2): 接口统一走 axios 封装并接入 element-ui 组件
6c6f209 fix(frontend-vue2): 事件名统一为 Vue2 规范的 kebab-case 并新增语法检查脚本
2af8052 fix(frontend-vue2): 修复流式渲染不逐步显示与设置界面 MCP 模块无法展示
90b2b2c refactor(init): 应用初始化逻辑抽离为独立 initialization 模块
efbae15 refactor(logging, memory): 日志初始化收敛到 initialization 模块并重构记忆管理
1d2fecb feat(scripts): 新增 Pod 容器内存监控与自愈脚本 memory_guard.sh
27368e2 refactor(prompts): 提示词目录化管理并优化三段提示词
4ad62ca refactor(config): system_prompt_path 更名为 prompt_path，提示词统一从该目录加载
ab79ecd refactor(tests): 测试目录按主程序层级划分
a5b6ad7 docs(api): 新增前后端接口文档 Excel（68 个接口 + WS）
d3bf91c Merge branch 'feat/frontend-vue2' into master
e887711 chore(frontend): 删除与项目无关的 logo 组件
b6ef643 docs(spec): 当前用户名注入上下文设计
444faa2 feat(agent): 系统提示词与执行环境注入当前用户名
58aaec4 feat: 上下文摘要与落库修复、思考 token 拆分、前端 UI 优化
41f880d feat: 工号登录体系、context_length 重命名、工作区重做与前端交互优化
800e30d refactor(model): 图片过滤升级为通用媒体内容处理，移除 supports_vision
bcc90eb feat: 工作区与设置页重构、会话恢复与颜色令牌；后端单配置迁移
1a8a7f6 feat(frontend-vue2): Vue2.6 选项式 API、移除 Tailwind、代码展示优化与登录页开关
431152e 修改marked版本
79c8477 登录模块优化与行内一直
d226c05 优化eslint报错
9119659 refactor(services): 简化 streaming/stream_processor，新增执行命令提示并同步兜底提示词
edf2314 feat(ui): 工具调用改为 dsh 风格卡片（默认收起，显示名称/参数/结果）
57a221a fix(auth): 空闲登出以后端配置为准，401 打印登出原因 - 前端 idleLogoutMs 默认改为 0，取到 /agent/auth/config 前不启用，避免误登出 - 401 拦截器与流式响应打印 detail，便于区分空闲超时/被顶下线/token 失效
5744359 feat(ui): 工具卡片按类型显示图标与标签 - 卡片头部按类型显示图标：命令/列出/搜索/读取/编辑 - 类型标签统一浅色常规字重；写入/编辑标题已含动作不再重复标签 - ls/glob 标签为「列出」，execute 为「命令」
10d8413 style(ui): 执行过程冻结态改为淡顶边+柔和阴影 - 冻结时不再用与静态边框同深的实线，改为 6% 淡顶边 + 向下柔和阴影，避免滚动时突兀硬线 - 补充暗色主题下的淡边与阴影取值
5463609 fix(auth): 登录页可配置默认开、恢复 URL 免密直登；空闲登出改由后端 401 决定 - LOGIN_PAGE_ENABLED 改为读构建期 VUE_APP_LOGIN_PAGE_ENABLED，未配置默认开（本地可用登录页切换用户） - 恢复 handlePasswordlessUrlLogin（?username=&user_id= 直登），initApp 中优先执行 - 移除前端本地空闲登出计时器（idleLogoutMs/resetIdleTimer 等），超时统一由后端返回 401 触发登出
6a0b709 fix(dev): 关闭 webpack-dev-server gzip 压缩，修复 SSE 流式被缓冲 - devServer.compress 默认 true 会对 text/event-stream 做 gzip，事件被攒到流结束才一次性到达 - 表现为「后端已 step2，前端仍显示正在响应」，直连后端正常、经代理才复现
801c3f2 feat(ui): 工作区/会话面板折叠图标改为 chevron
52ee607 chore(deps): marked 升级至 17 并重构 markdown 渲染 - marked 4.3.0 -> ^17.0.5，markdownSetup 统一渲染器与扩展 - ChatMessage 改用共享 renderMarkdown；FilePreview 同步适配
77a8cf9 优化代码显示高亮
91f03be Revert "chore(deps): marked 升级至 17 并重构 markdown 渲染"
a0831a7 Revert "feat(ui): 工作区/会话面板折叠图标改为 chevron"
1591871 fix(ui): 会话流式状态与渲染一致性 - 多会话流式隔离：流事件按所属会话写入其 sessionStates 缓冲，避免 A 流式中新建/切换 B 时内容串台 - 流结束（列表移除"进行中"标记）后调用历史接口以服务端数据回填，修复"已完成仍显示执行中/内容不完整" - 删除当前会话后复用切会话/新建逻辑回退，不再残留空欢迎页 - 兜底：流结束时若消息仍标记 loading 则强制收尾
d03ca3f fix(ui): 切会话拉取历史期间显示骨架屏，避免闪空欢迎页 - 新增 sessionLoading 门控：加载历史时不渲染空欢迎页，改用骨架屏 - 加载期间输入框保持贴底，避免居中→贴底跳动
```

