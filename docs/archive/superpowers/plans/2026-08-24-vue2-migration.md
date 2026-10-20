# Vue 2 Full Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Vue 3/Vite frontend with a complete Vue 2/Vue CLI application that uses Element UI and a unified Axios API layer while preserving every existing feature.

**Architecture:** First replace the build system and runtime bootstrap, then introduce the shared request layer, then migrate components in functional groups. Vue 2 components use Options API; Element UI supplies standard controls; custom components retain the specialized chat, workspace, markdown, and preview layouts.

**Tech Stack:** Vue 2.7, Vue CLI 5/Webpack, Element UI 2.15, Axios, Tailwind CSS, existing preview/editor libraries adapted to Vue 2 lifecycle wrappers.

---

## File Structure

- Modify: `frontend/package.json` — Vue 2, Vue CLI, Element UI, Axios dependencies and scripts.
- Create: `frontend/vue.config.js` — Webpack dev server, environment mode handling, Monaco handling.
- Create: `frontend/babel.config.js` — Vue CLI Babel preset and Element UI transpilation.
- Modify: `frontend/src/main.js` — Vue 2 root instance and Element UI initialization.
- Modify: `frontend/src/config.js` — replace `import.meta.env` with Vue CLI `process.env`.
- Create: `frontend/src/api/request.js` — unified Axios instance and interceptors.
- Modify: `frontend/src/api/auth.js`, `chat.js`, `files.js`, `scheduledTasks.js`, `settings.js`, `skills.js` — use the unified request layer.
- Modify: all 27 files in `frontend/src/components/` and `frontend/src/App.vue` — convert to Vue 2 Options API and Element UI-compatible behavior.
- Modify: `frontend/index.html` — Vue CLI entry markup.
- Modify: `frontend/scripts/run-vite.mjs` — replace Vite mode dispatch with Vue CLI mode dispatch.
- Modify: `frontend/scripts/generate-runtime-config.sh` and `frontend/scripts/print-serve-env.mjs` only if their output contract changes.

## Conversion Contract

Every Vue SFC must obey these mappings:

```text
<script setup>                         -> <script> with export default
defineProps({...})                     -> props: {...}
defineEmits([...])                     -> event names used directly through this.$emit
const state = ref(value)               -> data() { return { state: value } }
const value = computed(() => expr)     -> computed: { value() { return expr } }
watch(source, handler)                 -> watch: { source: handler }
onMounted(fn)                          -> mounted() { fn() }
onBeforeUnmount(fn)                    -> beforeDestroy() { fn() }
onUnmounted(fn)                        -> destroyed() { fn() }
nextTick(fn)                           -> this.$nextTick(fn)
state.value                            -> this.state
props.value                            -> this.value or this.$props.value
emit('name', payload)                  -> this.$emit('name', payload)
<Teleport to="body">...</Teleport>     -> Element UI dialog/popover or body-mounted Vue 2 portal
v-model:propName                       -> :propName.sync or explicit prop/event pair
shallowRef(value)                      -> non-reactive instance property assigned in created()
onActivated(fn)                        -> activated() { fn() }
```

Vue 2 event names must use lowercase or kebab-case. Existing camelCase event names are changed to kebab-case at both emitter and parent.

## Task 1: Vue CLI Build Baseline

**Files:**
- Modify: `frontend/package.json`
- Create: `frontend/vue.config.js`
- Create: `frontend/babel.config.js`
- Modify: `frontend/index.html`
- Modify: `frontend/src/main.js`
- Modify: `frontend/src/config.js`
- Modify: `frontend/scripts/run-vite.mjs`

- [ ] **Step 1: Replace dependencies and scripts**

Use these production dependencies:

```json
{
  "dependencies": {
    "axios": "^1.7.9",
    "docx": "^9.6.0",
    "docx-preview": "^0.3.7",
    "element-ui": "^2.15.14",
    "exceljs": "^3.4.0",
    "gemoji": "^8.1.0",
    "highlight.js": "^11.11.1",
    "jspdf": "^4.2.0",
    "katex": "^0.16.47",
    "mammoth": "^1.12.0",
    "markdown-it": "^14.1.1",
    "marked": "^17.0.5",
    "marked-emoji": "^2.0.3",
    "marked-katex-extension": "^5.1.10",
    "monaco-editor": "^0.53.0",
    "pdfjs-dist": "^4.8.69",
    "shiki": "^3.22.0",
    "vue": "^2.7.16",
    "xlsx": "^0.18.5"
  }
}
```

Use these development dependencies:

```json
{
  "devDependencies": {
    "@iconify/json": "^2.2.44",
    "@tailwindcss/postcss": "^4.1.17",
    "@vue/cli-plugin-babel": "~5.0.8",
    "@vue/cli-service": "~5.0.8",
    "autoprefixer": "^10.4.24",
    "postcss": "^8.5.6",
    "tailwindcss": "^4.1.17",
    "unplugin-icons": "^23.0.1",
    "vue-cli-plugin-element": "^1.0.1"
  }
}
```

Use these scripts:

```json
{
  "scripts": {
    "dev": "node scripts/run-vite.mjs dev",
    "build": "node scripts/run-vite.mjs build && sh scripts/generate-runtime-config.sh",
    "preview": "vue-cli-service preview",
    "serve": "sh scripts/generate-runtime-config.sh && node scripts/print-serve-env.mjs && npx --yes serve -s dist/"
  }
}
```

- [ ] **Step 2: Create Vue CLI configuration**

Create `frontend/vue.config.js`:

```js
const { defineConfig } = require('@vue/cli-service')
const Icons = require('unplugin-icons/webpack').default

module.exports = defineConfig({
  transpileDependencies: ['element-ui'],
  productionSourceMap: false,
  configureWebpack: {
    plugins: [
      Icons({ autoInstall: true }),
    ],
    devServer: {
      host: process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0',
      port: Number(process.env.PORT || 5173),
    },
  },
})
```

Create `frontend/babel.config.js`:

```js
module.exports = {
  presets: ['@vue/cli-plugin-babel/preset'],
}
```

- [ ] **Step 3: Replace bootstrap and environment access**

Replace `frontend/src/main.js` with:

```js
import Vue from 'vue'
import ElementUI from 'element-ui'
import 'element-ui/lib/theme-chalk/index.css'
import './style.css'
import 'highlight.js/styles/github-dark.css'
import App from './App.vue'

Vue.use(ElementUI)
Vue.config.productionTip = false

new Vue({
  render: (h) => h(App),
}).$mount('#app')
```

In `frontend/src/config.js`, replace every `import.meta.env.VITE_*` expression with the matching `process.env.VUE_APP_*` expression:

```text
import.meta.env.MODE          -> process.env.NODE_ENV
import.meta.env.VITE_API_BASE_URL -> process.env.VUE_APP_API_BASE_URL
import.meta.env.VITE_APP_TITLE -> process.env.VUE_APP_TITLE
import.meta.env.VITE_APP_WELCOME_TITLE -> process.env.VUE_APP_WELCOME_TITLE
```

Update all four `.env.*` files so each `VITE_*` key also has a matching `VUE_APP_*` key. Keep `VITE_*` keys only if the runtime generator still reads them.

Update `frontend/scripts/run-vite.mjs` so it invokes:

```js
spawnSync(
  require.resolve('@vue/cli-service/bin/vue-cli-service.js'),
  mode === 'dev' ? ['serve'] : ['build'],
  { stdio: 'inherit', env: process.env, cwd: root }
)
```

- [ ] **Step 4: Verify baseline**

Run:

```bash
cd frontend && npm install && npm run build
```

Expected result: build may report unresolved migrated components, but Webpack must recognize Vue CLI, Babel, and the new entry point. Fix build-system errors before Task 2.

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vue.config.js frontend/babel.config.js frontend/index.html frontend/src/main.js frontend/src/config.js frontend/scripts/run-vite.mjs frontend/.env.dev frontend/.env.prod frontend/.env.test frontend/.env.win
git commit -m "build(frontend): 切换 Vue CLI 与 Vue 2 基线"
```

## Task 2: Unified Axios Layer

**Files:**
- Create: `frontend/src/api/request.js`
- Modify: `frontend/src/api/auth.js`
- Modify: `frontend/src/api/chat.js`
- Modify: `frontend/src/api/files.js`
- Modify: `frontend/src/api/scheduledTasks.js`
- Modify: `frontend/src/api/settings.js`
- Modify: `frontend/src/api/skills.js`

- [ ] **Step 1: Create the request module**

Create `frontend/src/api/request.js`:

```js
import axios from 'axios'
import { API_BASE_URL } from '../config.js'

export const AUTH_EXPIRED_EVENT = 'auth-expired'
export const USER_ACTIVITY_EVENT = 'user-activity'
export const TOKEN_KEY = 'mini_agent_token'
export const USERNAME_KEY = 'mini_agent_username'

export function dispatchUserActivity() {
  window.dispatchEvent(new CustomEvent(USER_ACTIVITY_EVENT))
}

export function dispatchAuthExpired() {
  window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT))
}

export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function getStoredUsername() {
  return localStorage.getItem(USERNAME_KEY)
}

export function storeAuth(token, username) {
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USERNAME_KEY, username)
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USERNAME_KEY)
}

export function getAuthHeaders() {
  const token = getStoredToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export const request = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
})

request.interceptors.request.use((config) => {
  dispatchUserActivity()
  return {
    ...config,
    headers: {
      ...config.headers,
      ...getAuthHeaders(),
    },
  }
})

request.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response && error.response.status === 401) {
      clearAuth()
      dispatchAuthExpired()
    }
    return Promise.reject(error)
  }
)

export async function requestJson(config, fallbackMessage = '请求失败') {
  try {
    const response = await request(config)
    return response.data
  } catch (error) {
    const detail = error.response && error.response.data && error.response.data.detail
    const message = detail || error.message || fallbackMessage
    throw new Error(message)
  }
}

export async function requestBlob(config) {
  const response = await request({
    ...config,
    responseType: 'blob',
  })
  return response.data
}
```

- [ ] **Step 2: Convert standard API calls**

Use `requestJson` for all JSON endpoints. Use `requestBlob` for downloads. Convert each function with this exact pattern:

```js
export function getModels() {
  return requestJson({ url: '/api/settings/models', method: 'get' }, '获取模型列表失败')
}
```

Preserve all exported function names and argument signatures so components do not change call sites in this task.

- [ ] **Step 3: Preserve streaming fetch**

Keep `attachStream`, `sendMessage`, `resumeStream`, and `createNewChat` on `fetch` because Axios does not expose incremental response bodies. Move their shared URL, headers, activity event, and 401 handling into helpers exported from `request.js`:

```js
export function streamUrl(path) {
  return `${API_BASE_URL}${path}`
}

export function streamHeaders(extraHeaders = {}) {
  return {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...extraHeaders,
  }
}

export async function handleStreamResponse(response) {
  if (response.status === 401) {
    clearAuth()
    dispatchAuthExpired()
    throw new Error('登录已过期，请重新登录')
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw new Error(detail.detail || `请求失败: ${response.status}`)
  }
  return response
}
```

- [ ] **Step 4: Verify API layer**

Run:

```bash
cd frontend && npm run build
```

Expected result: no unresolved API imports and no direct Axios/Fetch calls outside `request.js`, `chat.js`, preview download paths, and explicit browser file downloads.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api
git commit -m "refactor(frontend): 统一 Axios 接口封装"
```

## Task 3: Foundation Components and Root Shell

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/components/EasyLogo.vue`
- Modify: `frontend/src/components/CqLogo.vue`
- Modify: `frontend/src/components/WuKongLogo.vue`
- Modify: `frontend/src/components/FileIcon.vue`
- Modify: `frontend/src/components/ConfirmDialog.vue`
- Modify: `frontend/src/components/Welcome.vue`
- Modify: `frontend/src/components/SessionList.vue`

- [ ] **Step 1: Convert foundation components**

Apply the Conversion Contract to each file. For logo and icon components, move computed sizing to `computed`. For `FileIcon.vue`, keep the existing `~icons/*` imports and expose the selected icon through a computed property.

- [ ] **Step 2: Replace ConfirmDialog with Element UI dialog behavior**

Keep the exported component name, props, and events. Internally use `<el-dialog>` and preserve these methods:

```js
methods: {
  show() {
    this.visible = true
    return new Promise((resolve) => {
      this.$once('confirm', () => resolve(true))
      this.$once('cancel', () => resolve(false))
    })
  },
  handleConfirm() {
    this.visible = false
    this.$emit('confirm')
    this.$emit('confirmed')
  },
  handleCancel() {
    this.visible = false
    this.$emit('cancel')
    this.$emit('canceled')
  },
}
```

Update parent components to call `this.$refs.confirmDialog.show()`.

- [ ] **Step 3: Migrate login and session shell**

Convert `Welcome.vue` and `SessionList.vue` to Options API. Use Element UI for:

- Login/register forms: `el-form`, `el-form-item`, `el-input`, `el-button`
- Session actions: `el-dropdown`, `el-dropdown-menu`, `el-dropdown-item`
- Rename dialog: `el-dialog`
- Feedback: `this.$message` and `this.$message.error`

Preserve the session grouping, pinning, keyboard shortcuts, sidebar collapse behavior, user menu, and all emitted events.

- [ ] **Step 4: Convert App root**

Convert `App.vue` to Options API. Preserve:

- All session state and stream recovery
- Chat state and current session handling
- Idle logout
- Theme switching
- Runtime config loading
- All panel visibility flags
- Component event wiring

Change camelCase parent listeners to kebab-case, for example:

```html
<SessionList
  @create-session="handleCreateSession"
  @select-session="handleSelectSession"
  @show-user-management="showUserManagementPanel = true"
/>
```

- [ ] **Step 5: Verify foundation**

Run:

```bash
cd frontend && npm run build
```

Expected result: Webpack build reaches the chat and panel components. Foundation components contain no `<script setup>`, `defineProps`, `defineEmits`, or `<Teleport>`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.vue frontend/src/components/EasyLogo.vue frontend/src/components/CqLogo.vue frontend/src/components/WuKongLogo.vue frontend/src/components/FileIcon.vue frontend/src/components/ConfirmDialog.vue frontend/src/components/Welcome.vue frontend/src/components/SessionList.vue
git commit -m "feat(frontend): 迁移 Vue 2 基础组件"
```

## Task 4: Chat Pipeline

**Files:**
- Modify: `frontend/src/components/Chat.vue`
- Modify: `frontend/src/components/ChatInput.vue`
- Modify: `frontend/src/components/ChatMessage.vue`
- Modify: `frontend/src/components/TodoListPanel.vue`

- [ ] **Step 1: Convert TodoListPanel**

Move expanded state to `data`, counts to `computed`, and preserve the existing collapse animation and todo status rendering.

- [ ] **Step 2: Convert ChatInput**

Convert all refs, computed properties, watchers, lifecycle hooks, file upload state, model dropdown, token ring, and keyboard behavior to Options API. Replace the two `<Teleport>` blocks with Element UI `el-popover` or an absolutely positioned dropdown; preserve current visual placement.

- [ ] **Step 3: Convert ChatMessage**

Move non-reactive Shiki highlighters to instance properties created in `beforeCreate`. Preserve:

- Markdown and math rendering
- Tool call blocks
- Reasoning blocks
- Approval controls
- Retry and file actions
- Generated-file modal trigger

Use `this.$nextTick` after DOM-affecting watches.

- [ ] **Step 4: Convert Chat**

Convert scroll state, message refs, navigation, lifecycle cleanup, welcome screen, preset questions, and child events to Options API. Preserve the current center/bottom composer modes.

- [ ] **Step 5: Verify chat pipeline**

Run:

```bash
cd frontend && npm run build
rg -n "<script setup|defineProps|defineEmits|<Teleport" frontend/src/components/Chat.vue frontend/src/components/ChatInput.vue frontend/src/components/ChatMessage.vue frontend/src/components/TodoListPanel.vue
```

Expected result: build succeeds and the `rg` command returns no matches.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Chat.vue frontend/src/components/ChatInput.vue frontend/src/components/ChatMessage.vue frontend/src/components/TodoListPanel.vue
git commit -m "feat(frontend): 迁移 Vue 2 聊天链路"
```

## Task 5: Files, Workspace, and Preview

**Files:**
- Modify: `frontend/src/components/AssetsPanel.vue`
- Modify: `frontend/src/components/FileTreeNode.vue`
- Modify: `frontend/src/components/GeneratedFilesModal.vue`
- Modify: `frontend/src/components/WorkspacePanel.vue`
- Modify: `frontend/src/components/FilePreview.vue`
- Modify: `frontend/src/components/CodePreview.vue`
- Modify: `frontend/src/components/DocxPreview.vue`
- Modify: `frontend/src/components/ExcelPreview.vue`
- Modify: `frontend/src/components/PdfPreview.vue`
- Modify: `frontend/src/components/PptPreview.vue`

- [ ] **Step 1: Convert file management panels**

Convert `AssetsPanel`, `FileTreeNode`, `GeneratedFilesModal`, and `WorkspacePanel` to Options API. Replace direct authenticated `fetch` calls with `request`/`requestBlob`. Use Element UI dialogs, dropdowns, loading, messages, and confirmations.

- [ ] **Step 2: Replace Vue Office components**

Remove `@vue-office/docx`, `@vue-office/excel`, `@vue-office/pptx`, and `vue-demi`. For DOCX and Excel previews, use the existing `docx-preview` and `ExcelJS` logic in dedicated Vue 2 components. For PPTX, use the existing conversion/download path or an embedded preview URL; do not retain a Vue 3 component wrapper.

- [ ] **Step 3: Convert preview components**

Convert `FilePreview`, `CodePreview`, `DocxPreview`, `ExcelPreview`, `PdfPreview`, and `PptPreview` to Options API. Store editor, highlighter, PDF document, and DOM observer objects as non-reactive instance properties. Dispose them in `beforeDestroy`.

For PDF.js under Webpack, import the worker with:

```js
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf'
import pdfjsWorker from 'pdfjs-dist/legacy/build/pdf.worker.entry'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker
```

- [ ] **Step 4: Verify file group**

Run:

```bash
cd frontend && npm run build
rg -n "@vue-office|vue-demi|<script setup|defineProps|defineEmits|<Teleport" frontend/src/components
```

Expected result: build succeeds and the command returns no matches.

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/components/AssetsPanel.vue frontend/src/components/FileTreeNode.vue frontend/src/components/GeneratedFilesModal.vue frontend/src/components/WorkspacePanel.vue frontend/src/components/FilePreview.vue frontend/src/components/CodePreview.vue frontend/src/components/DocxPreview.vue frontend/src/components/ExcelPreview.vue frontend/src/components/PdfPreview.vue frontend/src/components/PptPreview.vue
git commit -m "feat(frontend): 迁移 Vue 2 文件与预览"
```

## Task 6: Settings, Skills, Tasks, Users, and Profile

**Files:**
- Modify: `frontend/src/components/SettingsPanel.vue`
- Modify: `frontend/src/components/SkillCenter.vue`
- Modify: `frontend/src/components/ScheduledTasksPanel.vue`
- Modify: `frontend/src/components/UserProfile.vue`
- Modify: `frontend/src/components/UserManagementPanel.vue`

- [ ] **Step 1: Convert SettingsPanel**

Use Element UI tabs, forms, switches, inputs, buttons, dialogs, loading, messages, and notifications. Preserve memory editing, system prompt editing, skill listing, model selection, MCP server management, and market synchronization.

- [ ] **Step 2: Convert SkillCenter**

Replace `<Teleport>` with `el-dialog`. Preserve public/user skill tabs, import, add, remove, download, and close behavior.

- [ ] **Step 3: Convert ScheduledTasksPanel**

Convert task list, run history, task detail, file tree, preview, toggling, deletion, and immediate execution. Use Element UI tables, dialogs, switches, buttons, messages, and confirm dialogs.

- [ ] **Step 4: Convert profile and user management**

Use Element UI forms and dialogs. Preserve profile loading/update, logout, account deletion, user list, password reset, pagination state, and admin-only behavior.

- [ ] **Step 5: Verify management group**

Run:

```bash
cd frontend && npm run build
rg -n "<script setup|defineProps|defineEmits|<Teleport" frontend/src/components/SettingsPanel.vue frontend/src/components/SkillCenter.vue frontend/src/components/ScheduledTasksPanel.vue frontend/src/components/UserProfile.vue frontend/src/components/UserManagementPanel.vue
```

Expected result: build succeeds and `rg` returns no matches.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SettingsPanel.vue frontend/src/components/SkillCenter.vue frontend/src/components/ScheduledTasksPanel.vue frontend/src/components/UserProfile.vue frontend/src/components/UserManagementPanel.vue
git commit -m "feat(frontend): 迁移 Vue 2 管理面板"
```

## Task 7: Full Regression and Cleanup

**Files:**
- Modify: all remaining frontend files containing Vue 3 syntax
- Modify: `frontend/src/utils/mcpMarket.test.js` only if import behavior changes

- [ ] **Step 1: Remove Vue 3 remnants**

Run:

```bash
rg -n "createApp|<script setup|defineProps|defineEmits|defineExpose|<Teleport|v-model:[A-Za-z]|import\\.meta\\.env|@vue-office|vue-demi" frontend/src frontend/package.json frontend/vite.config.js
```

Delete `frontend/vite.config.js` after confirming Vue CLI build is authoritative.

- [ ] **Step 2: Run unit and build checks**

Run:

```bash
cd frontend && node --test src/utils/mcpMarket.test.js && npm run build
```

Expected result: one Node test passes and the production build succeeds.

- [ ] **Step 3: Verify environment modes**

Run each build mode and confirm the printed mode:

```bash
cd frontend
AGENT_ENV=dev npm run build
AGENT_ENV=test npm run build
AGENT_ENV=prod npm run build
```

Expected result: all three builds succeed and report the matching environment.

- [ ] **Step 4: Manual browser regression**

Run backend and frontend locally, then verify:

1. Login and registration
2. Session create, select, rename, pin, delete
3. Message send and streaming render
4. File upload, preview, download, delete
5. Workspace tree and generated files
6. Settings, models, MCP, skills
7. Scheduled tasks
8. Profile and user management
9. Light/dark themes
10. Runtime welcome title override

- [ ] **Step 5: Final commit**

```bash
git status --porcelain
git add frontend
git commit -m "refactor(frontend): 完成 Vue 2 全量迁移"
```
