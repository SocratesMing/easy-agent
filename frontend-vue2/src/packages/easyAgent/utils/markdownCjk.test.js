import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createMarkdownRenderer,
  linkifyText,
  renderMarkdown,
  setupMarkedExtensions,
} from '../shared/markdown/index.js'

setupMarkedExtensions()
const renderer = createMarkdownRenderer({ highlight: (code) => `<pre class="hljs"><code>${code}</code></pre>` })
const render = (md) => renderMarkdown(md, renderer).trim()

test('单个 ~ 是范围符，同一段里出现两处也不会配成删除线', () => {
  assert.equal(render('1.2495~2505、1.2497~2506渲染一下'), '<p>1.2495~2505、1.2497~2506渲染一下</p>')
  assert.equal(render('白天 20~30℃，夜间 15~18℃'), '<p>白天 20~30℃，夜间 15~18℃</p>')
  assert.equal(render('约3~5天，或者10~20天'), '<p>约3~5天，或者10~20天</p>')
})

test('~~双波浪线~~ 仍是删除线，紧挨中文引号也能识别', () => {
  assert.equal(render('这个~~方案~~作废'), '<p>这个<del>方案</del>作废</p>')
  assert.equal(render('这个~~“旧方案”~~作废'), '<p>这个<del>“旧方案”</del>作废</p>')
})

test('加粗紧挨中文标点与汉字时也能识别，不再露出星号或错配', () => {
  assert.equal(render('**注意：**请勿删除该文件'), '<p><strong>注意：</strong>请勿删除该文件</p>')
  assert.equal(render('**收益率（年化）**为 5%'), '<p><strong>收益率（年化）</strong>为 5%</p>')
  assert.equal(render('**结论是可行的。**接下来说明原因'), '<p><strong>结论是可行的。</strong>接下来说明原因</p>')
  assert.equal(
    render('这是**“重点”**内容，以及**《标题》**后文'),
    '<p>这是<strong>“重点”</strong>内容，以及<strong>《标题》</strong>后文</p>'
  )
})

test('原有的强调写法不受影响', () => {
  // 2026-10-15 升级 marked 时的回归用例：内容以 ASCII 标点结尾、结尾 ** 后紧跟全角标点
  assert.equal(
    render('**放量破位、呈"三连阴"**：10/13 收盘'),
    '<p><strong>放量破位、呈&quot;三连阴&quot;</strong>：10/13 收盘</p>'
  )
  assert.equal(render('这是**重点**内容，*斜体*也行'), '<p>这是<strong>重点</strong>内容，<em>斜体</em>也行</p>')
  assert.equal(render('Hello **world**! and *it* works'), '<p>Hello <strong>world</strong>! and <em>it</em> works</p>')
  assert.equal(render('snake_case_name 与 a_b_c'), '<p>snake_case_name 与 a_b_c</p>')
})

test('用户消息按纯文本展示（UserMessageText → linkifyText）：markdown 符号原样保留，只有网址可点击', () => {
  assert.equal(
    linkifyText('1.2495~2505、1.2497~2506 **粗** __init__.py List<String> C:\\Users\\_build\n运行结果\n====='),
    '1.2495~2505、1.2497~2506 **粗** __init__.py List&lt;String&gt; C:\\Users\\_build\n运行结果\n====='
  )
  assert.equal(
    linkifyText('帮我总结一下这篇文章 https://example.com/post/123'),
    '帮我总结一下这篇文章 <a href="https://example.com/post/123" target="_blank" rel="noopener noreferrer">' +
      'https://example.com/post/123</a>'
  )
})
