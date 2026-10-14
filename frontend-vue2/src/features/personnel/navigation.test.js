import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

const frontendRoot = path.resolve(import.meta.dirname, '../../..')

test('admin personnel and permission entry lives in the knowledge top bar', async () => {
  const sessionList = await readFile(path.join(frontendRoot, 'src/components/SessionList.vue'), 'utf8')
  const workbench = await readFile(path.join(frontendRoot, 'src/features/knowledge/KnowledgeWorkbench.vue'), 'utf8')
  const app = await readFile(path.join(frontendRoot, 'src/App.vue'), 'utf8')

  assert.doesNotMatch(sessionList, /人员管理/)
  assert.match(workbench, /v-if="teamSpaceManagement\.is_admin && selectedSpace === 'team'"[\s\S]{0,500}人员管理/)
  assert.match(workbench, /\$emit\('manage-personnel'\)/)
  assert.match(app, /@manage-personnel="handleShowUserManagement"/)
})

test('personnel page explains global team-space authorization and its scope', async () => {
  const source = await readFile(path.join(frontendRoot, 'src/features/personnel/PersonnelManagement.vue'), 'utf8')

  assert.match(source, /公共空间授权管理/)
  assert.match(source, /本部门成员默认可见（只读）/)
  assert.match(source, /admin 默认拥有全局权限，无需给自己授权/)
  assert.match(source, /「权限管理」中按部门、处室、团队勾选配置/)
  assert.match(source, />\{\{ isTeamManager\(user\) \? '可创建\/管理' : '不可创建' \}\}<\/strong>/)
})

test('team-space viewing is default-on, no per-user viewer switch', async () => {
  const source = await readFile(path.join(frontendRoot, 'src/features/personnel/PersonnelManagement.vue'), 'utf8')
  const api = await readFile(path.join(frontendRoot, 'src/features/personnel/api.js'), 'utf8')

  assert.doesNotMatch(source, /可查看|toggleTeamViewer|teamViewer/)
  assert.doesNotMatch(api, /team-space-viewers|TeamSpaceViewer/)
})
