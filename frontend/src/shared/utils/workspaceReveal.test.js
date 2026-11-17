// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/utils/workspaceReveal.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  collectWorkspaceFiles,
  createWorkspaceReveal,
  workspaceHasNewFiles,
} from './workspaceReveal.js'

function listing(items) {
  return { items }
}

describe('workspaceHasNewFiles', () => {
  it('treats a new path or a changed size as generated', () => {
    const before = new Map([['a.txt', 1]])
    assert.equal(workspaceHasNewFiles(before, new Map([['a.txt', 1]])), false)
    assert.equal(workspaceHasNewFiles(before, new Map([['a.txt', 2]])), true)
    assert.equal(workspaceHasNewFiles(before, new Map([['a.txt', 1], ['b.txt', 3]])), true)
    assert.equal(workspaceHasNewFiles(null, new Map([['a.txt', 1]])), false)
  })
})

describe('collectWorkspaceFiles', () => {
  it('walks nested directories and ignores folders themselves', async () => {
    const tree = {
      '': listing([
        { type: 'directory', path: 'out' },
        { type: 'file', path: 'readme.md', size: 4 },
      ]),
      out: listing([{ type: 'file', path: 'out/report.xlsx', size: 9 }]),
    }
    const files = await collectWorkspaceFiles(async path => tree[path])
    assert.equal(files.get('readme.md'), 4)
    assert.equal(files.get('out/report.xlsx'), 9)
    assert.equal(files.size, 2)
  })
})

describe('createWorkspaceReveal', () => {
  it('expands only after the turn when a file appeared', async () => {
    let files = new Map()
    const delays = []
    const revealer = createWorkspaceReveal(
      async () => listing([...files].map(([path, size]) => ({ type: 'file', path, size }))),
      { delay: ms => { delays.push(ms); return Promise.resolve() } },
    )
    revealer.remember('s1')
    files = new Map([['out/a.txt', 2]])
    let expanded = 0
    let refreshed = 0
    await revealer.reveal({
      sessionId: 's1',
      stillCurrent: () => true,
      refresh: () => { refreshed += 1 },
      onExpand: () => { expanded += 1 },
    })
    assert.equal(refreshed, 1)
    assert.equal(expanded, 1)
    assert.deepEqual(delays, [300])
  })

  it('does nothing when the session changed before the snapshot settled', async () => {
    const revealer = createWorkspaceReveal(
      async () => listing([]),
      { delay: () => Promise.resolve() },
    )
    revealer.remember('s1')
    let expanded = 0
    await revealer.reveal({
      sessionId: 's1',
      stillCurrent: () => false,
      refresh: () => {},
      onExpand: () => { expanded += 1 },
    })
    assert.equal(expanded, 0)
  })
})
