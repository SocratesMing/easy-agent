import assert from 'node:assert/strict'
import test from 'node:test'
import { createMarkdownRenderer, renderMarkdownBlocks } from '../markdown/index.js'
import { isFenceClosed, visualKindOfLang, visualPendingHtml, visualPlaceholderHtml } from './fence.js'

const renderer = createMarkdownRenderer({
  highlight: (code) => `<pre><code>${code.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</code></pre>`,
})
const html = (content, partial) => renderMarkdownBlocks(content, renderer, { partial }).map((b) => b.html).join('')

test('只有 echarts / mermaid / widget / html 算可视化代码块', () => {
  assert.equal(visualKindOfLang('echarts'), 'echarts')
  assert.equal(visualKindOfLang('Mermaid'), 'mermaid')
  assert.equal(visualKindOfLang('widget title=x'), 'widget')
  assert.equal(visualKindOfLang('html'), 'html')
  assert.equal(visualKindOfLang('HTM'), 'html')
  for (const lang of ['', 'json', 'js', 'chart', 'toString', 'constructor']) {
    assert.equal(visualKindOfLang(lang), null, lang)
  }
})

test('围栏闭合判定：结束围栏要与开头同类、不短于开头', () => {
  assert.equal(isFenceClosed('```echarts\n{}\n```'), true)
  assert.equal(isFenceClosed('```echarts\n{}\n```\n'), true)
  assert.equal(isFenceClosed('````mermaid\nflowchart TD\n````'), true)
  assert.equal(isFenceClosed('```echarts\n{"a": 1'), false)
  assert.equal(isFenceClosed('```echarts'), false)
  assert.equal(isFenceClosed('````mermaid\nA\n```'), false)
  assert.equal(isFenceClosed('~~~mermaid\nA\n```'), false)
})

test('已闭合的可视化代码块：普通代码块外包一层占位元素，保留源码供挂载与回退', () => {
  const out = html('看图：\n\n```echarts\n{"series": [{"type": "bar", "data": [1, 2]}]}\n```\n')
  assert.match(out, /<div class="ea-visual" data-visual-kind="echarts"><div class="code-block-wrapper">/)
  assert.match(out, /&quot;series&quot;|"series"/)
  assert.doesNotMatch(out, /is-pending/)
})

test('流式中还没闭合时只出固定骨架，不露出半截 JSON', () => {
  const partial = html('```echarts\n{"series": [{"type": "ba', true)
  assert.equal(partial, visualPendingHtml('echarts'))
  // 同一个未闭合块继续增长，骨架 HTML 不变（块级 DOM 可以原样复用）
  assert.equal(html('```echarts\n{"series": [{"type": "bar", "data": [1', true), partial)
})

test('流式停顿后的最终帧仍不渲染未闭合的图表、流程图和 HTML', () => {
  for (const sample of [
    '```echarts\n{"series": [{"type": "ba"',
    '```mermaid\nflowchart TD\n  A -->',
    '```widget\n<div><script>broken',
  ]) {
    const settled = renderMarkdownBlocks(sample, renderer, { partial: false, streaming: true })
    assert.equal(settled.map(block => block.html).join(''), visualPendingHtml(sample.slice(3, sample.indexOf('\n'))))
  }
})

test('闭合后的占位块在流式中间帧与最终渲染时 HTML 相同（节点复用，iframe 不会重建）', () => {
  const text = '```mermaid\nflowchart TD\n  A --> B\n```\n\n后面还在写'
  const blocksPartial = renderMarkdownBlocks(text, renderer, { partial: true })
  const blocksFinal = renderMarkdownBlocks(text, renderer, { partial: false })
  assert.equal(blocksPartial[0].key, blocksFinal[0].key)
  assert.equal(blocksPartial[0].html, blocksFinal[0].html)
  assert.match(blocksFinal[0].html, /data-visual-kind="mermaid"/)
})

test('最终渲染时模型忘了闭合围栏，也按写完处理', () => {
  const out = html('```widget\n<div>hi</div>', false)
  assert.match(out, /^<div class="ea-visual" data-visual-kind="widget">/)
})

test('html 代码块按页面渲染，流式未闭合时只出骨架', () => {
  const closed = html('```html\n<!DOCTYPE html>\n<html><body><h1>报告</h1></body></html>\n```')
  assert.match(closed, /data-visual-kind="html"/)
  assert.doesNotMatch(closed, /is-pending/)
  assert.equal(html('```html\n<!DOCTYPE html>\n<html><body><h1>报', true), visualPendingHtml('html'))
})

test('占位与骨架的结构', () => {
  assert.equal(visualPlaceholderHtml('echarts', '<pre></pre>'), '<div class="ea-visual" data-visual-kind="echarts"><pre></pre></div>')
  assert.match(visualPendingHtml('widget'), /交互组件生成中…/)
})
