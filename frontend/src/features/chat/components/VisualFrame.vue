<template>
  <!-- 工具卡片里的图表：结构与 markdown 的可视化占位一致，挂载逻辑在 shared/visual/mount.js -->
  <div ref="root" class="ea-visual" :data-visual-kind="kind"><pre class="ea-visual-source"><code>{{ source }}</code></pre></div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { API_BASE_URL } from '../../../config.js'
import { VISUAL_FRAME_PATH } from '../../../shared/visual/fence.js'
import { mountVisual } from '../../../shared/visual/mount.js'

const props = defineProps({
  /** echarts | mermaid | widget */
  kind: { type: String, default: 'echarts' },
  /** 交给沙箱框架页的源码：ECharts 为 JSON 文本 */
  source: { type: String, default: '' },
  title: { type: String, default: '' },
})

const root = ref(null)

onMounted(() => {
  mountVisual(root.value, {
    frameUrl: `${API_BASE_URL}${VISUAL_FRAME_PATH}`,
    kind: props.kind,
    source: props.source,
    title: props.title,
  })
})
</script>

<style src="./visual.css"></style>
