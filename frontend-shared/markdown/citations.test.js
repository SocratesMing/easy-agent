import assert from 'node:assert/strict'
import test from 'node:test'
import { relatedEvidence, linkKnowledgeCitations } from './citations.js'
import { createMarkdownRenderer, renderMarkdown } from './index.js'
import { citationPosition, citationViewport } from '../citations/popover.js'

test('正文分块的首次引用顺序与资料卡片一致，忽略代码和无效编号', () => {
  const evidence = [{ document_id: 'a' }, { document_id: 'b' }, { document_id: 'c' }]
  const message = {
    knowledge_evidence: evidence,
    content: '[知识依据1]',
    blocks: [
      { type: 'content', order: 2, content: '[知识依据1] [知识依据3] [知识依据99]' },
      { type: 'content', order: 1, content: '`[知识依据2]` 文本[知识依据3]' },
      { type: 'thinking', content: '[知识依据2]' },
    ],
  }
  const result = relatedEvidence(message)
  assert.deepEqual(result.numberMap, { 3: 1, 1: 2 })
  assert.deepEqual(result.items, [evidence[2], evidence[0]])
  const html = renderMarkdown(linkKnowledgeCitations('[知识依据3] [知识依据1] [知识依据99]', result.numberMap, true), createMarkdownRenderer({}))
  assert.match(html, /class="ke-cite ke-cite-number" data-cite="1" aria-label="查看引用来源 1">1<\/button>/)
  assert.match(html, /data-cite="2"/)
  assert.match(html, /\[知识依据99\]/)
})

test('工作台默认保持文字引用；无引用的主聊天展示全部证据', () => {
  const html = renderMarkdown(linkKnowledgeCitations('[知识依据3]', { 3: 1 }), createMarkdownRenderer({}))
  assert.match(html, /<a class="ke-cite" data-cite="1">\[知识依据1\]<\/a>/)
  assert.deepEqual(relatedEvidence({ content: '你好', knowledge_evidence: [{ snippet: '片段' }] }), {
    items: [{ snippet: '片段' }], numberMap: {},
  })
})

test('浮层在窗口边缘翻转并保持可见，窄屏限制宽度', () => {
  const anchor = { left: 990, top: 700, bottom: 720 }
  const position = citationPosition(anchor, { height: 280 }, { width: 1024, height: 768 })
  assert.equal(position.top, 412)
  assert.equal(position.left + position.width, 1012)
  const narrow = citationPosition({ left: 300, top: 50, bottom: 70 }, { height: 600 }, { width: 320, height: 480 })
  assert.equal(narrow.width, 296)
  assert.equal(narrow.left, 12)
  assert.ok(narrow.top >= 12)
  assert.ok(narrow.top + Math.min(600, narrow.maxHeight) <= 468)
})

test('浮层使用实际可见视口，支持缩放、软键盘和可见区域偏移', () => {
  const viewport = citationViewport({ innerWidth: 1200, innerHeight: 900,
    document: { documentElement: { clientWidth: 1200 } },
    visualViewport: { width: 400, height: 300, offsetLeft: 80, offsetTop: 240 } })
  assert.deepEqual(viewport, { left: 80, top: 240, width: 400, height: 300 })
  const position = citationPosition({ left: 470, top: 500, bottom: 520 }, { height: 500 }, viewport)
  assert.equal(position.left, 108)
  assert.equal(position.top, 252)
  assert.equal(position.width, 360)
  assert.equal(position.maxHeight, 276)
})

test('长资料高度不超过 320px，四个边缘和极小窗口都限制在视口内', () => {
  for (const viewport of [{ width: 1280, height: 580 }, { width: 320, height: 240 }]) {
    for (const anchor of [{ left: 2, top: 2, bottom: 22 },
      { left: viewport.width - 20, top: viewport.height - 24, bottom: viewport.height - 4 }]) {
      const result = citationPosition(anchor, { height: 1000 }, viewport)
      assert.equal(result.maxHeight, Math.min(320, viewport.height - 24))
      assert.equal(result.width, Math.min(360, viewport.width - 24))
      assert.ok(result.left >= 12)
      assert.ok(result.top >= 12)
      assert.ok(result.left + result.width <= viewport.width - 12)
      assert.ok(result.top + result.maxHeight <= viewport.height - 12)
    }
  }
})
