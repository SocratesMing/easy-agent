import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import test from 'node:test'
import { JSDOM } from 'jsdom'
import knowledgeCitationMixin from '../../shared/citations/knowledgeWorkbench.js'
import { relatedEvidence, linkKnowledgeCitations } from '../../shared/markdown/citations.js'
import { createMarkdownRenderer, renderMarkdown } from '../../shared/markdown/index.js'

const baselines = [
  ['Vue 2', new URL('./KnowledgeWorkbench.vue', import.meta.url)],
  ['Vue 3', new URL('../../../../../../frontend/src/features/knowledge/KnowledgeWorkbench.vue', import.meta.url)],
]

for (const [baseline, path] of baselines) {
  test(`${baseline} 知识库当前与历史回答复用悬停引用，切换或收起时清理`, async () => {
    const dom = new JSDOM('<html><body><div id="mount"></div></body></html>')
    const keys = ['window', 'document', 'navigator', 'Element', 'HTMLElement', 'SVGElement', 'Node']
    const originals = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
    keys.forEach(key => Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true }))
    const Vue = createRequire(import.meta.url)('vue/dist/vue.common.js')
    let vm
    try {
      const source = readFileSync(path, 'utf8')
      // 执行实际面板的引用 Options 配置和问答模板；仅替代无关的工作台初始化与子组件。
      const options = new Function('knowledgeCitationMixin',
        'return ' + source.slice(source.indexOf('export default {') + 'export default '.length, source.indexOf('  components: {')) + '}')(knowledgeCitationMixin)
      const body = source.slice(source.indexOf('        <div class="ke-answer" ref="askBody"'), source.indexOf('          <div v-if="askError && !asking"')) + '</div>'
      const evidence = name => ({ document_name: `${name}.md`, snippet: `${name}的依据` })
      const message = name => ({ role: 'assistant', content: `${name}[知识依据2]`, knowledge_evidence: [evidence('未引用'), evidence(name)] })
      const ChatMessage = {
        props: ['message', 'citationPresentation'],
        render(h) {
          const { numberMap } = relatedEvidence(this.message)
          return h('div', { domProps: { innerHTML: renderMarkdown(linkKnowledgeCitations(
            this.message.content, numberMap, this.citationPresentation === 'popover'), createMarkdownRenderer({})) } })
        },
      }
      vm = new Vue({
        ...options,
        data: () => ({ askHistory: [{ question: '历史问题', message: message('历史') }, { question: '空条目' }],
          agentMessage: message('当前'), selectedBase: null, questionSuggestions: [], askOpen: true, selectedBaseId: 'a', lastQuestion: '当前问题', asking: false }),
        components: { ChatMessage, IconSparkles: { render: h => h('span') } },
        ...Vue.compile(body),
      }).$mount('#mount')
      const anchors = vm.$el.querySelectorAll('.ke-cite-number')
      assert.equal(anchors.length, 2)
      assert.equal(vm.messages.length, 3)
      anchors[0].dispatchEvent(new dom.window.MouseEvent('mouseover', { bubbles: true }))
      assert.equal(vm.citationPreview.item.document_name, '历史.md')
      anchors[1].dispatchEvent(new dom.window.MouseEvent('mouseover', { bubbles: true }))
      assert.equal(vm.citationPreview.item.document_name, '当前.md')
      assert.equal(vm.citationPreview.number, 1)
      vm.askOpen = false
      await Vue.nextTick()
      assert.equal(vm.citationPreview, null)
      vm.previewCitation({ target: anchors[0] })
      vm.selectedBaseId = 'b'
      await Vue.nextTick()
      assert.equal(vm.citationPreview, null)
      vm.previewCitation({ target: anchors[0] })
      vm.askHistory = []
      vm.agentMessage = null
      await Vue.nextTick()
      assert.equal(vm.citationPreview, null)
      assert.equal(anchors[0].hasAttribute('aria-describedby'), false)
    } finally {
      if (vm) vm.$destroy()
      dom.window.close()
      keys.forEach((key, index) => {
        if (originals[index]) Object.defineProperty(globalThis, key, originals[index])
        else delete globalThis[key]
      })
    }
  })
}
