import assert from 'node:assert/strict'
import test from 'node:test'

import { createMarkdownRenderer, renderMarkdown, setupMarkedExtensions } from '../shared/markdown/index.js'

setupMarkedExtensions()
const renderer = createMarkdownRenderer({ highlight: (code) => `<pre class="hljs"><code>${code}</code></pre>` })
const render = (md) => renderMarkdown(md, renderer).trim()
/** 渲染出的公式个数（行内与块级的 KaTeX 根节点都是 span.katex） */
const formulas = (html) => (html.match(/<span class="katex">/g) || []).length

function assertFormulas(md, count, { display = false } = {}) {
  const html = render(md)
  assert.equal(formulas(html), count, `${md}\n${html}`)
  assert.ok(!html.includes('katex-error'), `不应出现 KaTeX 解析错误：${md}\n${html}`)
  if (display) assert.ok(html.includes('katex-display'), `应渲染成块级公式：${md}\n${html}`)
  return html
}

test('行内公式紧挨中文标点、汉字时也能识别，不再配错成一段报红', () => {
  assertFormulas('其中 $a$、$b$ 为常数', 2)
  assertFormulas('面积 $S=\\pi r^2$；周长 $C=2\\pi r$。', 2)
  assertFormulas('设变量$x$满足$x>0$即可', 2)
  assertFormulas('（$x$）和（$y$）', 2)
  assertFormulas('**收益率 $r=\\frac{P_1-P_0}{P_0}$**：按年化', 1)
})

test('认 \\( \\) 与 \\[ \\] 定界符', () => {
  assertFormulas('公式 \\(E=mc^2\\) 成立，且\\(a_1+b_1\\)也成立', 2)
  assertFormulas('推导如下：\n\\[\n\\frac{a}{b} + \\sqrt{x}\n\\]\n得证', 1, { display: true })
  assertFormulas('前文 \\[ x^2+y^2=1 \\] 后文', 1, { display: true })
  // \[1\] 多半是转义的方括号；LaTeX 的 \\[2pt]（换行加间距）不能被当成定界符
  assert.equal(render('见文献 \\[1\\] 与 \\[2\\]'), '<p>见文献 [1] 与 [2]</p>')
  assertFormulas('$$\n\\begin{aligned} a &= b \\\\[2pt] c &= d \\end{aligned}\n$$', 1, { display: true })
})

test('美元金额、shell 变量、转义的 $ 不会被当成公式', () => {
  for (const md of [
    '花了 $5，又花了 $10，共 $15',
    '单价$5，总价$100',
    '价格区间 $5-$10，或 $1、$2、$3',
    'US$5 与 US$10',
    'echo $HOME 和 $PATH，或者 $HOME/$PATH',
    '\\$5 与 \\$x\\$',
  ]) {
    assertFormulas(md, 0)
  }
})

test('代码里的 $ 与 \\( \\) 原样保留', () => {
  assert.equal(render('用 `$x$` 和 `\\(y\\)` 表示'), '<p>用 <code>$x$</code> 和 <code>\\(y\\)</code> 表示</p>')
  assertFormulas('```\n$x$ \\(y\\) \\[z\\]\n```', 0)
})

test('原有的公式写法不受影响', () => {
  assertFormulas('复数共轭 $z^*$ 与 $w^*$ 的乘积', 2)
  assertFormulas('$$\n\\int_0^1 x^2 dx\n$$', 1, { display: true })
  assertFormulas('结果为$$x^2$$，证毕', 1, { display: true })
})
