// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/citations/knowledgeWorkbench.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
import citationPopoverMixin from './popover.js'

/** 知识库问答的历史条目与当前回答适配到主聊天引用接口。 */
export default {
  mixins: [citationPopoverMixin],
  computed: {
    messages() {
      // 空条目也占用索引，与模板的 data-message-index 保持一致。
      const history = this.askHistory.map(entry => entry.message)
      return this.agentMessage ? [...history, this.agentMessage] : history
    },
  },
  watch: {
    selectedBaseId() { this.closeCitation() },
    askOpen(open) { if (!open) this.closeCitation() },
  },
  // Vue 2 的 beforeDestroy 由共享 mixin 提供；Vue 3 使用此钩子。
  beforeUnmount() { this.disposeCitations() },
}
