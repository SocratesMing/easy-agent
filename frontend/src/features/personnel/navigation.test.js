import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'

const frontendRoot = path.resolve(import.meta.dirname, '../../..')

test('admin personnel and permission entry lives in the knowledge top bar', async () => {
  const sessionList = await readFile(path.join(frontendRoot, 'src/components/SessionList.vue'), 'utf8')
  const workbench = await readFile(path.join(frontendRoot, 'src/features/knowledge/KnowledgeWorkbench.vue'), 'utf8')
  const app = await readFile(path.join(frontendRoot, 'src/App.vue'), 'utf8')

  assert.doesNotMatch(sessionList, /人员与权限/)
  assert.match(workbench, /v-if="teamSpaceManagement\.is_admin"[\s\S]{0,500}人员与权限/)
  assert.match(workbench, /\$emit\('managePersonnel'\)/)
  assert.match(app, /@managePersonnel="handleShowUserManagement"/)
})

test('personnel page explains global team-space authorization and its scope', async () => {
  const source = await readFile(path.join(frontendRoot, 'src/features/personnel/PersonnelManagement.vue'), 'utf8')

  assert.match(source, /团队空间创建与管理授权/)
  assert.match(source, /admin 默认拥有全局权限，无需给自己授权/)
  assert.match(source, /查看者、维护者和管理员/)
  assert.match(source, />\{\{ isTeamManager\(user\) \? '可创建\/管理' : '仅查看' \}\}<\/strong>/)
})
