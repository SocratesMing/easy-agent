/**
 * 结构约束（防劣化闸门）：文件行数、层间依赖、markdown 渲染单点。
 *
 * 这些用例不是"风格检查"，而是给重构留的ratchet：
 * - 行数预算只减不增：拆分文件后请把 `LINE_BUDGET` 里的数值调小或删掉条目；
 * - 新增文件不得突破 `MAX_NEW_FILE_LINES`；
 * - 组件不得新增对 `api/` 的直接依赖（老账记在 allowlist 里，还一笔删一行）；
 * - 只有 `src/shared/markdown/` 可以直接 import `marked`。
 *
 * 运行：`npm run test:unit`（node --test，不依赖 eslint）。
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

const frontendRoot = path.resolve(import.meta.dirname, '../..')
const srcRoot = path.join(frontendRoot, 'src')

/** 新文件的行数上限 */
const MAX_NEW_FILE_LINES = 800

/**
 * 现存超大文件的行数预算（= 2026-10-20 实测值，只允许往下调）。
 * 拆分这些文件时同步删掉条目，避免预算变成"合法上限"。
 */
const LINE_BUDGET = {
  'App.vue': 3136,
  'features/chat/components/ChatInput.vue': 3359,
  'features/chat/components/ChatMessage.vue': 2652,
  'features/chat/components/AiSidebar.vue': 1725,
  'features/experts/components/ExpertListPanel.vue': 1740,
  'features/chat/mixins/chatSession.js': 1205,
  'features/connectors/components/ConnectorListPanel.vue': 1491,
  'features/skills/components/SkillListPanel.vue': 1237,
  'shared/preview/FilePreview.vue': 1138,
  'features/settings/components/SettingsPanel.vue': 1128,
  'features/chat/components/Chat.vue': 1122,
  'features/chat/components/SessionList.vue': 1028,
  'features/tasks/components/ScheduledTasksPanel.vue': 967,
  'features/workspace/components/WorkspacePanel.vue': 908,
  'features/knowledge/KnowledgeWorkbench.vue': 1911,
  'features/personnel/PersonnelManagement.vue': 1145,
}

/** 组件里已经存在的 api 直连（存量，只允许删） */
const API_IMPORT_ALLOWLIST = new Set([
  'features/chat/components/ChatInput.vue',
  'features/chat/components/Welcome.vue',
  'features/connectors/components/ConnectorListPanel.vue',
  'features/experts/components/ExpertListPanel.vue',
  'shared/preview/FilePreview.vue',
  'shared/preview/FileTreeNode.vue',
  'features/connectors/components/McpConfigEditorPanel.vue',
  'features/tasks/components/ScheduledTasksPanel.vue',
  'features/settings/components/SettingsPanel.vue',
  'features/skills/components/SkillDetailPanel.vue',
  'features/skills/components/SkillListPanel.vue',
  'features/personnel/components/UserManagementPanel.vue',
  'features/workspace/components/WorkspacePanel.vue',
  'features/knowledge/KnowledgeWorkbench.vue',
  'features/personnel/PersonnelManagement.vue',
])

/** 允许 import marked 的位置（markdown 渲染单点） */
const MARKDOWN_IMPORT_ALLOWLIST = new Set(['shared/markdown/index.js'])

async function collectSourceFiles(directory, acc = []) {
  const entries = await readdir(directory, { withFileTypes: true })
  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      await collectSourceFiles(entryPath, acc)
    } else if (entry.isFile() && /\.(vue|js)$/.test(entry.name) && !entry.name.endsWith('.test.js')) {
      acc.push(entryPath)
    }
  }
  return acc
}

const relative = (filePath) => path.relative(srcRoot, filePath).split(path.sep).join('/')

test('超大文件只能变短，不能变长', async () => {
  const files = await collectSourceFiles(srcRoot)
  const violations = []

  for (const filePath of files) {
    const rel = relative(filePath)
    const lines = (await readFile(filePath, 'utf8')).split('\n').length
    const budget = LINE_BUDGET[rel]

    if (budget === undefined) {
      if (lines > MAX_NEW_FILE_LINES) {
        violations.push(`${rel} 新文件 ${lines} 行，超过 ${MAX_NEW_FILE_LINES} 行上限（请拆分）`)
      }
    } else if (lines > budget) {
      violations.push(`${rel} 由 ${budget} 行涨到 ${lines} 行（预算只减不增）`)
    }
  }

  assert.deepEqual(violations, [], violations.join('\n'))
})

test('组件不得新增对 api/ 的直接依赖', async () => {
  const files = await collectSourceFiles(srcRoot)
  const violations = []

  for (const filePath of files) {
    const rel = relative(filePath)
    // 只约束组件层；features 下的 mixins/services/composables 属于下层，允许调用 api
    if (!rel.startsWith('components/') && !/^features\/.*\/components\//.test(rel)) continue
    // feature 自己的 api 模块就是这一层的出口，允许存在
    if (rel.endsWith('/api.js')) continue
    const source = await readFile(filePath, 'utf8')
    // 允许 `../api/xxx`、`@/api/xxx`、`features/x/api/xxx` 这类引用形式
    const hitsApi = /from\s+['"][^'"]*\/api\/[^'"]+['"]/.test(source)
    if (hitsApi && !API_IMPORT_ALLOWLIST.has(rel)) {
      violations.push(`${rel} 直接 import 了 api/ 模块；请改为经由 services/ 或 composable`)
    }
  }

  assert.deepEqual(violations, [], violations.join('\n'))
})

test('markdown 解析收敛在 shared/markdown 单点', async () => {
  const files = await collectSourceFiles(srcRoot)
  const violations = []

  for (const filePath of files) {
    const rel = relative(filePath)
    if (MARKDOWN_IMPORT_ALLOWLIST.has(rel)) continue
    const source = await readFile(filePath, 'utf8')
    if (/from\s+['"]marked['"]/.test(source)) {
      violations.push(`${rel} 直接 import 了 marked；请使用 shared/markdown`)
    }
  }

  assert.deepEqual(violations, [], violations.join('\n'))
})

test('共享 markdown 内核与 frontend-shared 保持一致', async () => {
  const source = await readFile(
    path.resolve(frontendRoot, '../frontend-shared/markdown/index.js'),
    'utf8'
  )
  const copy = await readFile(path.join(srcRoot, 'shared/markdown/index.js'), 'utf8')
  assert.ok(
    copy.endsWith(source),
    'shared/markdown/index.js 与 frontend-shared/markdown/index.js 不一致，请执行 npm run sync:shared'
  )
})
