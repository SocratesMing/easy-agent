import test from 'node:test'
import assert from 'node:assert/strict'

import {
  applyOptimisticUploadProgress,
  createOptimisticUploadDocument,
  markOptimisticUploadStarted,
  mergeServerDocumentsWithOptimistic,
  optimisticUploadStatusText,
} from './uploadPresentation.js'

test('shows a selected upload immediately in the target folder', () => {
  const item = {
    optimisticId: 'upload-local-1',
    baseId: 'base-1',
    folderId: 'folder-1',
  }
  const optimistic = createOptimisticUploadDocument(
    { name: 'report.pdf', size: 123, type: 'application/pdf' },
    item,
    new Date('2026-09-07T00:00:00Z')
  )

  assert.equal(optimistic.id, 'upload-local-1')
  assert.equal(optimistic.status, 'pending')
  assert.equal(optimistic.uploadState, 'queued')
  assert.equal(optimistic.optimistic, true)
  assert.deepEqual(optimistic.allowed_actions, [])
})

test('each optimistic upload reports its own transfer progress', () => {
  const queued = { optimistic: true, uploadState: 'queued', progress: 0 }
  const uploading = { optimistic: true, uploadState: 'uploading', progress: 0.42 }
  const saving = { optimistic: true, uploadState: 'saving', progress: 1 }

  assert.equal(optimisticUploadStatusText(queued), '等待上传')
  assert.equal(optimisticUploadStatusText(uploading), '上传中 42%')
  assert.equal(optimisticUploadStatusText(saving), '上传处理中')
  // 服务端文档仍按解析状态展示，不复用上传进度文案
  assert.equal(optimisticUploadStatusText({ status: 'processing', progress: 0.3 }), '')
})

test('upload progress helpers update only the target document row', () => {
  const documents = [
    { id: 'doc-a', optimistic: true, uploadState: 'queued', progress: 0 },
    { id: 'doc-b', optimistic: true, uploadState: 'queued', progress: 0 },
  ]

  markOptimisticUploadStarted(documents, 'doc-a')
  applyOptimisticUploadProgress(documents, 'doc-a', 37)

  assert.equal(documents[0].uploadState, 'uploading')
  assert.equal(documents[0].progress, 0.37)
  // 同批其它文档保持自己的排队状态，不受这一篇进度影响
  assert.equal(documents[1].uploadState, 'queued')
  assert.equal(documents[1].progress, 0)

  applyOptimisticUploadProgress(documents, 'doc-a', 100)
  assert.equal(documents[0].uploadState, 'saving')
})

test('polling preserves only optimistic uploads for the current unfiltered folder', () => {
  const current = [
    { id: 'pending-a', optimistic: true, base_id: 'base-1', folder_id: 'folder-1' },
    { id: 'pending-b', optimistic: true, base_id: 'base-1', folder_id: 'folder-2' },
  ]
  const server = [{ id: 'server-1' }]

  assert.deepEqual(
    mergeServerDocumentsWithOptimistic(server, current, {
      baseId: 'base-1', folderId: 'folder-1', preserve: true,
    }).map(item => item.id),
    ['pending-a', 'server-1']
  )
  assert.deepEqual(
    mergeServerDocumentsWithOptimistic(server, current, {
      baseId: 'base-1', folderId: 'folder-1', preserve: false,
    }),
    server
  )
})

test('polling hides optimistic row once the same upload appears from server', () => {
  const current = [{
    id: 'upload-local-1',
    optimistic: true,
    base_id: 'base-1',
    folder_id: 'folder-1',
    name: 'report.pdf',
    size_bytes: 123,
  }]
  const server = [{
    id: 'server-1',
    base_id: 'base-1',
    folder_id: 'folder-1',
    name: 'report.pdf',
    size_bytes: 123,
  }]

  assert.deepEqual(
    mergeServerDocumentsWithOptimistic(server, current, {
      baseId: 'base-1', folderId: 'folder-1', preserve: true,
    }).map(item => item.id),
    ['server-1']
  )
})
