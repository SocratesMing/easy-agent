<template>
  <!-- 工具卡片里的图表：结构与 markdown 的可视化占位一致，挂载逻辑在 shared/visual/mount.js -->
  <div class="ea-visual" :data-visual-kind="kind"><pre class="ea-visual-source"><code>{{ source }}</code></pre></div>
</template>

<script>
import { VISUAL_FRAME_PATH } from '../../../shared/visual/fence.js'
import { mountVisual } from '../../../shared/visual/mount.js'
import { resolveUrl } from '../../../utils/request.js'

export default {
  name: 'VisualFrame',
  props: {
    /** echarts | mermaid | widget */
    kind: { type: String, default: 'echarts' },
    /** 交给沙箱框架页的源码：ECharts 为 JSON 文本 */
    source: { type: String, default: '' },
    title: { type: String, default: '' },
  },
  mounted() {
    mountVisual(this.$el, {
      frameUrl: resolveUrl(VISUAL_FRAME_PATH),
      kind: this.kind,
      source: this.source,
      title: this.title,
    })
  },
}
</script>

<style src="./visual.css"></style>
