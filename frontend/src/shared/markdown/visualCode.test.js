// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/markdown/visualCode.test.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import assert from 'node:assert/strict'
import test from 'node:test'

import {
  createMarkdownRenderer,
  renderMarkdown,
  setupMarkedExtensions,
} from './index.js'

setupMarkedExtensions()

test('echarts / mermaid / widget / html 源码不进入高亮器，普通代码块仍正常高亮', () => {
  const highlighted = []
  const renderer = createMarkdownRenderer({
    highlight: (code, lang) => {
      highlighted.push(lang)
      return `<pre class="hljs"><code>${code}</code></pre>`
    },
  })

  for (const kind of ['echarts', 'mermaid', 'widget', 'html']) {
    const html = renderMarkdown(`\`\`\`${kind}\nA < B\n\`\`\``, renderer)
    assert.ok(html.includes(`data-visual-kind="${kind}"`), html)
    assert.ok(html.includes('A &lt; B'), html)
  }
  assert.deepEqual(highlighted, [])

  renderMarkdown('```js\nconst a = 1\n```', renderer)
  assert.deepEqual(highlighted, ['js'])
})
