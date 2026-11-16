import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createMarkdownRenderer,
  linkifyText,
  renderMarkdown,
  setupMarkedExtensions,
} from '../shared/markdown/index.js'

setupMarkedExtensions()
const renderer = createMarkdownRenderer({ highlight: (code) => `<pre>${code}</pre>` })
const render = (md) => renderMarkdown(md, renderer).trim()
const NEW_TAB = 'target="_blank" rel="noopener noreferrer"'

test('回复中的裸链接、markdown 链接都在新标签页打开', () => {
  assert.ok(render('参考 https://example.com/a?b=1 了解').includes(`<a href="https://example.com/a?b=1" ${NEW_TAB}>`))
  assert.ok(render('见[文档](https://example.com/doc)说明').includes(`<a href="https://example.com/doc" ${NEW_TAB}>文档</a>`))
})

test('整段是链接的行内代码可点击，并保留代码外观', () => {
  assert.equal(
    render('地址 `https://example.com/x` 打开'),
    `<p>地址 <a href="https://example.com/x" ${NEW_TAB}><code>https://example.com/x</code></a> 打开</p>`
  )
  assert.equal(render('命令 `npm run dev`'), '<p>命令 <code>npm run dev</code></p>')
  assert.equal(render('`curl https://a.com`'), '<p><code>curl https://a.com</code></p>')
})

test('linkifyText：纯文本里的链接可点击，收尾标点不算进链接，其余内容转义', () => {
  assert.equal(
    linkifyText('URL: https://news.example.com/a.html。\n摘要 <b>x</b> & y'),
    `URL: <a href="https://news.example.com/a.html" ${NEW_TAB}>https://news.example.com/a.html</a>。\n摘要 &lt;b&gt;x&lt;/b&gt; &amp; y`
  )
  assert.equal(linkifyText('无链接'), '无链接')
  assert.equal(linkifyText(null), '')
})

test('linkifyText：引号不能跳出 href 属性', () => {
  assert.equal(
    linkifyText(`https://evil.example.com/"onmouseover='x'`),
    `<a href="https://evil.example.com/" ${NEW_TAB}>https://evil.example.com/</a>&quot;onmouseover=&#039;x&#039;`
  )
  assert.equal(
    linkifyText(`https://a.com/x'onclick='y`),
    `<a href="https://a.com/x&#039;onclick=&#039;y" ${NEW_TAB}>https://a.com/x&#039;onclick=&#039;y</a>`
  )
})

test('文件/文档预览（sourceCards: false）不把「标题 + 裸 URL」收成命名链接', () => {
  const md = '- 东方财富收评：https://example.com/bare\n- [独立行链接](https://example.com/standalone)'
  const doc = renderMarkdown(md, renderer, { sourceCards: false })
  assert.ok(!doc.includes('msg-source-card'))
  assert.ok(doc.includes(`<a href="https://example.com/bare" ${NEW_TAB}>https://example.com/bare</a>`))
  assert.ok(doc.includes(`<a href="https://example.com/standalone" ${NEW_TAB}>独立行链接</a>`))
  const chat = render(md)
  assert.ok(!chat.includes('msg-source-card'))
  assert.ok(chat.includes(`<a href="https://example.com/bare" ${NEW_TAB}>东方财富收评</a>`), chat)
  assert.ok(chat.includes(`<a href="https://example.com/standalone" ${NEW_TAB}>独立行链接</a>`), chat)
})

test('任务列表项带 task-list-item class（不依赖 :has()，旧版 Chrome 也能去掉圆点）', () => {
  const html = render('- [x] 完成\n- [ ] 未完成\n- 普通项')
  assert.equal((html.match(/<li class="task-list-item"><input /g) || []).length, 2)
  assert.ok(html.includes('<li>普通项</li>'))
})

test('表格单元格里的 markdown 链接可点击，且不被改写成别的样式', () => {
  const md = [
    '| # | 事件 | 来源 / 时间 |',
    '|---|---|---|',
    '| 1 | 中期选举 | [第一财经](https://www.yicai.com/a.html) 11-05 10:00 UTC；[NBC](https://www.nbcnews.com/x) 11-04 23:51 UTC |',
  ].join('\n')
  const html = render(md)
  assert.ok(html.includes('<table>'), html)
  // 表格内的链接与正文链接同样在新标签页打开
  assert.ok(html.includes(`<a href="https://www.yicai.com/a.html" ${NEW_TAB}>第一财经</a>`), html)
  assert.ok(html.includes(`<a href="https://www.nbcnews.com/x" ${NEW_TAB}>NBC</a>`), html)
  // 时间留在链接外面
  assert.ok(html.includes('11-05 10:00 UTC'), html)
  assert.ok(!html.includes('msg-source-card'), html)
})

