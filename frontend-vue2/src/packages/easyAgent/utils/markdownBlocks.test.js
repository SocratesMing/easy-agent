import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import test from 'node:test'
import { JSDOM } from 'jsdom'

import {
  bufferStreamingTail,
  createMarkdownRenderer,
  escapeHtml,
  renderMarkdown,
  renderMarkdownBlocks,
  setupMarkedExtensions,
} from '../shared/markdown/index.js'
import { linkKnowledgeCitations } from '../shared/markdown/citations.js'
import { patchMarkdownBlocks } from '../shared/markdown/blockDom.js'

setupMarkedExtensions()
const renderer = createMarkdownRenderer({
  highlight: (code) => `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`,
})

// ── 流式分块：中间帧也要命中缓存，key 不随模式变化 ──────────────────────────

test('流式中间帧也命中缓存：已完成的块不再重复渲染', () => {
  let calls = 0
  const counting = createMarkdownRenderer({
    highlight: (code) => { calls++; return `<pre>${escapeHtml(code)}</pre>` },
  })
  const prefix = '```js\nconst a = 1\n```\n\n第一段说明\n\n'
  renderMarkdownBlocks(prefix + '第二段写到一半', counting, { partial: true })
  assert.equal(calls, 1)
  renderMarkdownBlocks(prefix + '第二段写到一半，又多了几个字', counting, { partial: true })
  assert.equal(calls, 1) // 代码块已完成，命中中间帧缓存
})

test('中间帧与最终渲染的块 key 一致，DOM 才能在两者之间就地复用', () => {
  const md = '第一段\n\n- a\n- b\n\n第三段'
  const partial = renderMarkdownBlocks(md, renderer, { partial: true }).map((b) => b.key)
  const final = renderMarkdownBlocks(md, renderer).map((b) => b.key)
  assert.deepEqual(partial, final)
})

test('引用式链接的定义后到时，前面已缓存的块重新渲染，与整篇渲染一致', () => {
  const head = '见[文档][d]说明\n\n下一段'
  renderMarkdownBlocks(head, renderer) // 先把「定义还没出现」时的第一块写进缓存
  const md = head + '\n\n[d]: https://example.com/doc'
  const joined = renderMarkdownBlocks(md, renderer).map((b) => b.html).join('')
  assert.equal(joined, renderMarkdown(md, renderer))
  assert.ok(joined.includes('href="https://example.com/doc"'), joined)
})

// ── 缩进 4 格的嵌套列表 / 续行不是缩进代码块 ──────────────────────────────

test('嵌套列表、列表续行里的知识依据照常变成锚点，编号按映射重排', () => {
  for (const md of [
    '1. 结论\n    - 依据：营收增长[知识依据2]',
    '1. 结论\n\n    依据：营收增长[知识依据2]',
    '- 结论\n    续行[知识依据2]',
    '正文第一行\n    紧跟正文的缩进行[知识依据2]',
  ]) {
    const html = renderMarkdown(linkKnowledgeCitations(md, { 2: 1 }), renderer)
    assert.ok(html.includes('<a class="ke-cite" data-cite="1">[知识依据1]</a>'), `${md}\n${html}`)
  }
})

test('真正的代码（空行后的缩进代码块、列表里缩进的围栏）仍原样保留', () => {
  for (const md of [
    '说明：\n\n    print("[知识依据2]")',
    '- 步骤\n\n    ```python\n    print("[知识依据2]")\n    ```',
  ]) {
    const html = renderMarkdown(linkKnowledgeCitations(md, { 2: 1 }), renderer)
    assert.ok(!html.includes('ke-cite') && html.includes('[知识依据2]'), `${md}\n${html}`)
  }
})

// ── 末行缓冲 ──────────────────────────────────────────────────────────

test('块公式 $$ 还没闭合时，中间帧不露出 TeX 源码；代码里的 $$ 不算', () => {
  assert.equal(bufferStreamingTail('公式如下：\n$$\nE = mc^'), '公式如下：')
  const closed = '公式如下：\n$$\nE = mc^2\n$$\n继续'
  assert.equal(bufferStreamingTail(closed), closed)
  assert.equal(bufferStreamingTail('代码 `$$` 不算\n下一行'), '代码 `$$` 不算\n下一行')
  const html = renderMarkdownBlocks('公式如下：\n$$\nE = mc^', renderer, { partial: true }).map((b) => b.html).join('')
  assert.ok(!html.includes('$$') && !html.includes('mc^'), html)
})

test('markdown 内核不用正则后行断言（不支持的引擎会让整个模块解析失败）', async () => {
  const dir = new URL('../shared/markdown/', import.meta.url)
  for (const name of await readdir(dir)) {
    if (!name.endsWith('.js')) continue
    const source = await readFile(new URL(name, dir), 'utf8')
    assert.ok(!/\(\?<[!=]/.test(source), `${name} 里有后行断言`)
  }
})

// ── 块级 HTML 转义后按段落显示 ────────────────────────────────────────

test('以标签开头的行转义后包进段落，多行保留换行；details 块结构不变', () => {
  assert.equal(
    renderMarkdown('<div> 和 <span> 有什么区别？', renderer).trim(),
    '<p>&lt;div&gt; 和 &lt;span&gt; 有什么区别？</p>'
  )
  assert.equal(renderMarkdown('<div>\n第二行\n</div>', renderer).trim(), '<p>&lt;div&gt;<br>第二行<br>&lt;/div&gt;</p>')
  assert.equal(
    renderMarkdown('<details>\n<summary>展开</summary>\n\n内容\n\n</details>', renderer).trim(),
    '<details>\n<summary>展开</summary>\n\n<p>内容</p>\n</details>'
  )
})

// ── 分块写 DOM：不加包裹层、内容没变的块保留节点 ──────────────────────────

test('patchMarkdownBlocks：不加包裹层，内容没变的块保留原节点，变了的就地替换，多余的移除', () => {
  const root = new JSDOM('').window.document.createElement('div')
  let state = patchMarkdownBlocks(root, [], [
    { key: '0:a', html: '<p>一</p>\n' },
    { key: '1:b', html: '<p>二</p>\n' },
  ])
  assert.equal(root.innerHTML, '<p>一</p>\n<p>二</p>\n')
  const first = root.firstChild
  state = patchMarkdownBlocks(root, state, [
    { key: '0:a', html: '<p>一</p>\n' },
    { key: '1:c', html: '<p>二三</p>\n' },
    { key: '2:d', html: '<pre>代码</pre>' },
  ])
  assert.equal(root.innerHTML, '<p>一</p>\n<p>二三</p>\n<pre>代码</pre>')
  assert.equal(root.firstChild, first) // 第一块的节点没被重建（选区不丢）
  // 同一个 key 的 HTML 变了（如流式结束后代码块补上高亮）→ 只替换这一块
  state = patchMarkdownBlocks(root, state, [
    { key: '0:a', html: '<p>一</p>\n' },
    { key: '1:c', html: '<p>二三</p>\n' },
    { key: '2:d', html: '<pre class="hljs">代码</pre>' },
  ])
  assert.equal(root.innerHTML, '<p>一</p>\n<p>二三</p>\n<pre class="hljs">代码</pre>')
  assert.equal(root.firstChild, first)
  patchMarkdownBlocks(root, state, [{ key: '0:a', html: '<p>一</p>\n' }])
  assert.equal(root.innerHTML, '<p>一</p>\n')
  assert.equal(root.firstChild, first)
})
