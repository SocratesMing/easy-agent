import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPersonnelFilters,
  buildPersonnelPayload,
  filenameFromContentDisposition,
  formatPersonnelValidationDetail,
  normalizePersonnelPage,
  summarizeImportResult,
  updateTeamManagerIds,
  validatePersonnelForm,
} from './model.js'

test('updates team-space manager selection without mutating current state', () => {
  const current = new Set(['user-a'])
  const granted = updateTeamManagerIds(current, 'user-b', true)
  const revoked = updateTeamManagerIds(granted, 'user-a', false)

  assert.deepEqual([...current], ['user-a'])
  assert.deepEqual([...granted].sort(), ['user-a', 'user-b'])
  assert.deepEqual([...revoked], ['user-b'])
})

test('normalizes list responses without coupling the UI to a response envelope', () => {
  const page = normalizePersonnelPage({
    users: [{ id: 7, account: 'zhangsan', organization_id: 'market', status: '停用' }],
    count: 1,
  })

  assert.equal(page.total, 1)
  assert.deepEqual(page.items[0], {
    id: 7,
    account: 'zhangsan',
    organization_id: 'market',
    status: '停用',
    user_id: '7',
    username: 'zhangsan',
    display_name: '',
    employee_id: '',
    department_id: 'market',
    department_name: '',
    email: '',
    position: '',
    mobile: '',
    account_status: 'disabled',
    personnel_source: '',
  })
})

test('uses array length when an unwrapped list has no total field', () => {
  assert.equal(normalizePersonnelPage([{ username: 'alice' }]).total, 1)
})

test('trims form values and does not put a password in personnel payloads', () => {
  const payload = buildPersonnelPayload({
    username: ' zhangsan ',
    display_name: ' 张三 ',
    department_id: ' d-01 ',
    department_name: ' 金融市场部 ',
    personnel_source: ' HR导出_2026-09 ',
    account_status: '启用',
  })

  assert.equal(payload.username, 'zhangsan')
  assert.equal(payload.display_name, '张三')
  assert.equal(payload.account_status, 'active')
  assert.equal(payload.source, 'HR导出_2026-09')
  assert.equal(Object.hasOwn(payload, 'password'), false)
})

test('requires the four import identity fields and a frontend-provided source', () => {
  const errors = validatePersonnelForm({ account_status: 'active' })

  assert.deepEqual(Object.keys(errors).sort(), [
    'department_id',
    'department_name',
    'display_name',
    'personnel_source',
    'username',
  ])
})

test('uses the same account-name rule as the backend', () => {
  const errors = validatePersonnelForm({
    username: '张 三',
    display_name: '张三',
    department_id: 'dept-market',
    department_name: '金融市场部',
    account_status: 'active',
    personnel_source: '手工录入',
  })

  assert.match(errors.username, /3-64/)

  const dotted = validatePersonnelForm({
    username: 'john.doe',
    display_name: 'John Doe',
    department_id: 'dept-market',
    department_name: '金融市场部',
    account_status: 'active',
    personnel_source: '手工录入',
  })
  assert.match(dotted.username, /下划线或连字符/)
})

test('builds only non-empty server-side filters', () => {
  assert.deepEqual(buildPersonnelFilters({
    keyword: ' 张三 ',
    accountStatus: 'active',
    departmentId: ' ',
  }), {
    keyword: '张三',
    account_status: 'active',
    page: 1,
    page_size: 50,
  })
})

test('normalizes and clamps personnel pagination filters', () => {
  assert.deepEqual(buildPersonnelFilters({ page: 3, pageSize: 1000 }), {
    page: 3,
    page_size: 200,
  })
  const page = normalizePersonnelPage({ items: [], total: 120, page: 3, page_size: 50 })
  assert.equal(page.page, 3)
  assert.equal(page.page_size, 50)
})

test('decodes RFC 5987 template filenames', () => {
  assert.equal(
    filenameFromContentDisposition("attachment; filename*=UTF-8''%E4%BA%BA%E5%91%98%E6%A8%A1%E6%9D%BF.xlsx"),
    '人员模板.xlsx'
  )
})

test('summarizes both compact and count-style import responses', () => {
  assert.equal(
    summarizeImportResult({ created_count: 2, updated_count: 3, skipped_count: 1 }),
    '导入完成：新增 2 人，更新 3 人，跳过 1 人'
  )
})

test('reports the full Excel error count when only the first errors are shown', () => {
  const message = formatPersonnelValidationDetail({
    message: 'Excel 校验失败',
    errors: Array.from({ length: 10 }, (_, index) => `第 ${index + 2} 行错误`),
  }, 8)

  assert.match(message, /共 10 条/)
  assert.match(message, /显示前 8 条/)
  assert.match(message, /另有 2 条未显示/)
  assert.doesNotMatch(message, /第 10 行错误/)
})
