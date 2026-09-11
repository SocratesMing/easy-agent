import test from 'node:test'
import assert from 'node:assert/strict'

import {
  createOptimisticUploadDocument,
  mergeServerDocumentsWithOptimistic,
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
  assert.equal(optimistic.optimistic, true)
  assert.deepEqual(optimistic.allowed_actions, [])
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
