import test from 'node:test'
import assert from 'node:assert/strict'

import { createConnectorsApi } from './connectors.js'
import { createExpertApi } from './expert.js'
import { buildUploadLimitError, createFilesApi } from './files.js'
import { createScheduledTasksApi } from './scheduledTasks.js'
import { createSettingsApi } from './settings.js'
import { createSkillsApi } from './skills.js'

function mockRequest(responses = {}) {
  const calls = []
  const fn = async (config, fallback) => {
    calls.push({ config, fallback })
    const key = `${(config.method || 'get').toUpperCase()} ${config.url}`
    if (key in responses) return responses[key]
    return { ok: true }
  }
  return { fn, calls }
}

test('createExpertApi forwards canonical routes and skips empty sessionId', async () => {
  const { fn, calls } = mockRequest()
  const api = createExpertApi(fn)

  assert.deepEqual(await api.getSessionExperts(''), { items: [] })
  assert.equal(calls.length, 0)

  await api.listExperts({ keyword: 'quant', page: 2, pageSize: 10 })
  assert.equal(calls[0].config.url, '/agent/list')
  assert.deepEqual(calls[0].config.params, {
    keyword: 'quant',
    status: '',
    page: 2,
    page_size: 10,
    include_disabled: false,
  })
})

test('createFilesApi skips non-numeric file ids and normalizes upload limit detail', async () => {
  const { fn, calls } = mockRequest()
  const api = createFilesApi(fn, fn)

  assert.deepEqual(await api.deleteFile('s1', { id: 'local-tmp' }), { status: 'skipped' })
  assert.equal(calls.length, 0)

  await api.deleteFile('s1', { id: 42 })
  assert.equal(calls[0].config.url, '/agent/files/42')
  assert.equal(calls[0].config.method, 'delete')

  const err = buildUploadLimitError({
    code: 'FILE_TOO_LARGE',
    message: '超出 10MB 限制',
    limit_mb: 10,
    actual_mb: 12.5,
  })
  assert.equal(err.message, '超出 10MB 限制')
  assert.equal(err.code, 'FILE_TOO_LARGE')
  assert.equal(err.limitMb, 10)
  assert.equal(err.actualMb, 12.5)
})

test('createConnectorsApi rejects non-JSON HTML fallback responses', async () => {
  const { fn } = mockRequest({ 'GET /agent/connectors': '<!doctype html><html></html>' })
  const api = createConnectorsApi(fn)

  await assert.rejects(() => api.getConnectors(), /后端未返回 JSON/)
})

test('createScheduledTasksApi / createSettingsApi / createSkillsApi hit expected endpoints', async () => {
  const { fn, calls } = mockRequest()
  await createScheduledTasksApi(fn).toggleScheduledTask('t-1')
  await createSettingsApi(fn).updateApprovalSettings('auto')
  await createSkillsApi(fn, fn).enableSkill('xlsx')

  assert.deepEqual(
    calls.map((c) => `${c.config.method.toUpperCase()} ${c.config.url}`),
    [
      'PATCH /agent/scheduled-tasks/t-1/toggle',
      'PUT /agent/settings/approval',
      'POST /agent/skill-center/enable-skill',
    ]
  )
})
