// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/utils/fileReferences.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import test from 'node:test'
import { referenceQuery, addReference, referenceMessage, referenceDraft, restoreReferenceDraft, initialReferenceSelection } from '../fileReferences.js'

test('@ 触发只匹配光标前的独立查询，不拦截邮件和已有正文', () => {
  assert.deepEqual(referenceQuery('总结 @报告', 6), { start: 3, end: 6, query: '报告' })
  assert.deepEqual(referenceQuery('@\n后文', 1), { start: 0, end: 1, query: '' })
  assert.equal(referenceQuery('a@example.com', 13), null)
  assert.equal(referenceQuery('总结 @a 后文', 8), null)
  assert.equal(referenceQuery('', null), null)
})

test('引用保留相对路径及类型、去重，并区分同名嵌套文件', () => {
  let refs = addReference([], { path: 'output/报告.md', type: 'file', size: 42, name: '报告.md' })
  assert.deepEqual(refs, [{ path: 'output/报告.md', type: 'file' }])
  assert.strictEqual(addReference(refs, refs[0]), refs)
  refs = addReference(refs, { path: 'output/底稿/报告.md', type: 'file' })
  refs = addReference(refs, { path: 'output', type: 'directory' })
  assert.equal(refs.length, 3)
  assert.equal(referenceMessage('', refs), '查看引用对象：output/报告.md、output/底稿/报告.md、output/')
  assert.equal(referenceMessage('  用户正文  ', refs), '用户正文')
})

test('沿用草稿键恢复引用，兼容纯文本且不跨会话恢复', () => {
  const refs = [{ path: 'output/带 空格.md', type: 'file' }]
  const raw = referenceDraft('总结', refs, 'A')
  assert.deepEqual(restoreReferenceDraft(raw, 'A'), { message: '总结', fileReferences: refs })
  assert.deepEqual(restoreReferenceDraft(raw, 'B'), { message: '总结', fileReferences: [] })
  assert.deepEqual(restoreReferenceDraft(raw, null), { message: '总结', fileReferences: [] })
  assert.equal(referenceDraft('旧草稿', [], 'A'), '旧草稿')
  for (const text of ['旧草稿', '{"用户正文":"JSON"}', 'null', '']) {
    assert.deepEqual(restoreReferenceDraft(text, 'A'), { message: text, fileReferences: [] })
  }
})

test('侧边栏异步恢复会话前保留草稿绑定，编辑或切换后不带入旧引用', () => {
  const refs = [{ path: 'output', type: 'directory' }]
  const raw = referenceDraft('侧栏草稿', refs, 'A')
  assert.equal(referenceDraft('侧栏草稿', [], null, raw), raw)
  assert.deepEqual(initialReferenceSelection(raw, '侧栏草稿', 'A', null), refs)
  assert.deepEqual(initialReferenceSelection(raw, '侧栏草稿', 'B', null), [])
  assert.deepEqual(initialReferenceSelection(raw, '侧栏草稿', 'A', 'B'), [])
  assert.deepEqual(initialReferenceSelection(raw, '已编辑', 'A', null), [])
  assert.equal(referenceDraft('已编辑', [], null, raw), '已编辑')
  assert.equal(referenceDraft('侧栏草稿', [], 'A', raw), '侧栏草稿')
})
