import assert from 'node:assert/strict'
import test from 'node:test'

import { processingElapsedText } from './presentation.js'

const now = Date.parse('2026-09-04T12:10:00Z')

test('processing timer follows the active operation for the same document', () => {
  const document = {
    id: 'document-1',
    size_bytes: 1024,
    created_at: '2026-09-04T10:00:00Z',
    updated_at: '2026-09-04T11:00:00Z',
  }
  const operations = [
    { resource_id: 'another-document', status: 'running', created_at: '2026-09-04T12:09:00Z' },
    { resource_id: 'document-1', status: 'failed', created_at: '2026-09-04T10:00:00Z' },
    { resource_id: 'document-1', status: 'running', created_at: '2026-09-04T12:07:00Z' },
  ]

  assert.equal(processingElapsedText(document, operations, now), '已用时 3 分钟')
})

test('large-file label depends on size rather than elapsed time', () => {
  const base = {
    id: 'document-1',
    created_at: '2026-09-04T10:00:00Z',
    updated_at: '2026-09-04T10:00:00Z',
  }

  assert.equal(
    processingElapsedText({ ...base, size_bytes: 1024 }, [], now),
    '已用时 130 分钟'
  )
  assert.equal(
    processingElapsedText({ ...base, size_bytes: 20 * 1024 * 1024 }, [], now),
    '大文件 · 已用时 130 分钟'
  )
})
