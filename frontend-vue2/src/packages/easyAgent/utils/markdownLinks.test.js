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
