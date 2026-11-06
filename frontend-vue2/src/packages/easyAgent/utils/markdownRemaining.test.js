import assert from 'node:assert/strict'
import test from 'node:test'

import {
  bufferStreamingTail,
  createMarkdownRenderer,
  escapeHtml,
  renderMarkdown,
  renderMarkdownBlocks,
  setupMarkedExtensions,
} from '../shared/markdown/index.js'
import { linkKnowledgeCitations } from '../shared/markdown/citations.js'

setupMarkedExtensions()
const renderer = createMarkdownRenderer({
  highlight: (code) => `<pre class="hljs"><code>${escapeHtml(code)}</code></pre>`,
})
const render = (md, options) => renderMarkdown(md, renderer, options)
const renderAllow = (md) => renderMarkdown(md, renderer, { rawHtml: 'allow' })
const NEW_TAB = 'target="_blank" rel="noopener noreferrer"'

// ── 第 5 项：模型回复里的尖括号 / 原始 HTML 吞字 ──────────────────────────

test('正文里的尖括号与未知标签按字面显示，不再吞字、不再整段变粗', () => {
  assert.equal(render('if a<b and c>d 时成立'), '<p>if a&lt;b and c&gt;d 时成立</p>\n')
  assert.equal(render('Vec<T> 与 Option<Box<T>>'), '<p>Vec&lt;T&gt; 与 Option&lt;Box&lt;T&gt;&gt;</p>\n')
  assert.equal(
    render('请解释 List<String> 和 Map<String, Integer>'),
    '<p>请解释 List&lt;String&gt; 和 Map&lt;String, Integer&gt;</p>\n'
  )
})

test('危险的原始 HTML 变成可见文本，脚本 / 标签不会进入 DOM', () => {
  const html = render('<div> 和 <span> 有什么区别？<script> 标签放哪里')
  assert.ok(html.includes('有什么区别？'), html)
  assert.ok(html.includes('&lt;div&gt;') && html.includes('&lt;script&gt;'), html)
  assert.ok(!html.includes('<script') && !html.includes('<div>'), html)
  assert.equal(
    render('保留 <!-- 内部备注 --> 后面的文字'),
    '<p>保留 &lt;!-- 内部备注 --&gt; 后面的文字</p>\n'
  )
  const img = render('结论 <img src=x onerror="alert(1)"> 后文')
  assert.ok(!img.includes('<img') && img.includes('&lt;img'), img)
})

test('小白名单标签仍可用，文件预览 rawHtml=allow 保持作者写的结构', () => {
  assert.ok(render('<details><summary>展开</summary>内容</details>').includes('<details><summary>展开</summary>内容</details>'))
  assert.ok(render('第<kbd>Ctrl</kbd>行<br>下一行').includes('<kbd>Ctrl</kbd>') && render('第<kbd>Ctrl</kbd>行<br>下一行').includes('<br>'))
  assert.ok(renderAllow('<div> 和 <span> 有什么区别？').includes('<div>'))
})

// ── 第 6 项：来源卡片误判 ────────────────────────────────────────────────

test('命令行、完整句子、表格数据行不再被整行改写成来源卡片', () => {
  for (const md of [
    '克隆仓库：git clone https://github.com/foo/bar.git',
    '安装方法参考官方文档：https://docs.python.org/3/install/',
    '下载：curl -O https://a.com/x.tgz',
  ]) {
    const html = render(md)
    assert.ok(!html.includes('msg-source-card'), `${md} => ${html}`)
    assert.ok(html.includes('https://'), `${md} => ${html}`)
  }
  const table = render('名称 | 链接\n--- | ---\n官网 | https://a.com')
  assert.ok(!table.includes('msg-source-card'), table)
})

test('真正的来源行仍生成卡片，标题与地址完整保留', () => {
  const html = render('东方财富收评 https://finance.eastmoney.com/a/1.html')
  assert.ok(html.includes(`<a class="msg-source-card" href="https://finance.eastmoney.com/a/1.html" ${NEW_TAB}`), html)
  assert.ok(html.includes('<span class="msg-source-card-title">东方财富收评</span>'), html)
  assert.ok(render('[独立行链接](https://example.com/standalone)').includes('class="msg-source-card"'))
  assert.ok(render('凤凰财经/中国证券报 https://a.com/x').includes('class="msg-source-card"'))
})

// ── 第 7 项：emoji 短代码误伤 ────────────────────────────────────────────

test('路径 / 比例 / 状态码里的 :xx: 不再被替换成 emoji', () => {
  for (const md of [
    'root:x:0:0:root:/root:/bin/bash',
    '比例 a:b:c',
    'status:ok:200',
    ':id: :on: :cn: :new: :cool:',
  ]) {
    const html = render(md)
    assert.ok(!html.includes('github-emoji'), `${md} => ${html}`)
  }
  assert.ok(render('root:x:0:0:root:/root:/bin/bash').includes('root:x:0:0:root:/root:/bin/bash'))
  assert.equal(
    render('好的 :rocket:').trim(),
    '<p>好的 <span class="github-emoji" role="img" aria-label=":rocket:">🚀</span></p>'
  )
})

// ── 第 10 项：预处理对各类代码块的保护 ──────────────────────────────────

test('~~~ 围栏、缩进代码块、多反引号围栏里的内容逐字不变', () => {
  const cases = [
    '~~~\n官网 https://a.com\n公式 $$x$$\n~~~\n\n正文',
    '    官网 https://a.com\n    公式 $$x$$',
    '````\n```js\n官网 https://a.com\n```\nprint("[知识依据1]")\n````',
  ]
  for (const md of cases) {
    const html = render(linkKnowledgeCitations(md, {}))
    assert.ok(!html.includes('msg-source-card'), `${md} => ${html}`)
    assert.ok(!html.includes('class="katex"'), `${md} => ${html}`)
    assert.ok(!html.includes('ke-cite'), `${md} => ${html}`)
    assert.ok(html.includes('https://a.com'), `${md} => ${html}`)
    assert.ok(html.includes('$$x$$') || html.includes('[知识依据1]'), `${md} => ${html}`)
  }
})

test('行内代码（含双反引号）里的知识依据与公式不被改写', () => {
  const html = render(linkKnowledgeCitations('说明 `` a`b [知识依据1] $$x$$ `` 结束', {}))
  assert.ok(!html.includes('ke-cite') && !html.includes('class="katex"'), html)
  assert.ok(html.includes('[知识依据1]'), html)
  const cite = render(linkKnowledgeCitations('依据 [知识依据2] 如上', { 2: 1 }))
  assert.equal(cite.trim(), '<p>依据 <a class="ke-cite" data-cite="1">[知识依据1]</a> 如上</p>')
})

test('带知识依据锚点的行里，裸 URL 后的中文不会被吞进 href', () => {
  const html = render(linkKnowledgeCitations('依据 [知识依据1]，详见 https://a.com/x。这是下一句', {}))
  assert.ok(html.includes(`<a href="https://a.com/x" ${NEW_TAB}>https://a.com/x</a>。这是下一句`), html)
})

// ── 第 11 项：流式中间帧不出现结构跳变 ──────────────────────────────────

test('缓冲末行：setext 标题、半截表格、未闭合强调/公式不露出', () => {
  assert.equal(bufferStreamingTail('以下是要点：\n-'), '以下是要点：')
  assert.equal(bufferStreamingTail('| a | b |\n|---'), '| a | b |')
  assert.equal(bufferStreamingTail('这段**还没写完'), '')
  assert.equal(bufferStreamingTail('$$\nx^2'), '') // 块公式未闭合：先不显示，不露出 TeX 源码（交接文档第 11 项）
  assert.equal(bufferStreamingTail('```\n$$\nx^2'), '```\n$$\nx^2') // 未闭合代码围栏整段保留
  const mid = render('以下是要点：\n-', { partial: true })
  assert.ok(!mid.includes('<h2>'), mid)
  assert.ok(!render('这是 **重点', { partial: true }).includes('**'))
  // 流结束后按原文完整渲染，结果与非流式一致
  assert.equal(render('以下是要点：\n- 第一条'), render('以下是要点：\n- 第一条', { partial: false }))
})

// ── 第 9 项：分块缓存与最终一致性 ────────────────────────────────────────

test('分块拼接与整篇渲染逐字一致', () => {
  const docs = [
    '第一段 **粗**\n\n- a\n- b\n\n| a | b |\n|:-|-:|\n| 1 | 2 |',
    '## 标题\n\n段落里有 $x^2$ 公式\n\n东方财富收评 https://finance.eastmoney.com/a/1.html',
    '```python\nprint(1)\n```\n\n结尾说明',
  ]
  for (const md of docs) {
    const full = render(md)
    const joined = renderMarkdownBlocks(md, renderer).map((b) => b.html).join('')
    assert.equal(joined, full, md)
  }
})

test('已完成块命中缓存，流式逐分片只重新高亮最后一块', () => {
  let calls = 0
  const counting = createMarkdownRenderer({
    highlight: (code) => { calls++; return `<pre>${escapeHtml(code)}</pre>` },
  })
  const prefix = '```js\nconst a = 1\n```\n\n第一段说明文字\n\n'
  renderMarkdownBlocks(prefix + '```js\nconst b = 2\n```', counting)
  assert.equal(calls, 2)
  renderMarkdownBlocks(prefix + '```js\nconst b = 2\n更长的代码\n```', counting)
  assert.equal(calls, 3) // 只多渲染了最后一个块
})

test('流式中间帧给高亮函数带 partial 标记，且不会污染最终渲染', () => {
  const seen = []
  const tracking = createMarkdownRenderer({
    highlight: (code, lang, state) => { seen.push(!!(state && state.partial)); return `<pre>${escapeHtml(code)}</pre>` },
  })
  const md = '```js\nconst a = 1\n```'
  renderMarkdown(md, tracking, { partial: true })
  renderMarkdown(md, tracking)
  assert.deepEqual(seen, [true, false])
})
