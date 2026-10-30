// ⚠️ 自动生成，请勿直接编辑。
// 源文件：frontend-shared/chart/echarts.js
// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）
/**
 * ECharts 运行时的共享入口（Vue2 / Vue3 共用）。
 *
 * 本文件由 ChartArtifactView 动态 import()，因此：
 * - ECharts 不进首屏主包，第一次出图时才下载；
 * - 用 `echarts/lib/...` 的细分入口按需注册：`echarts/charts` 桶文件是
 *   `export *` 聚合，webpack 无法 tree-shake，会把所有图表类型打进包里。
 *
 * 只注册 MCP 信封实际使用的 Bar / Line / Pie 与基础组件。
 */
import * as echarts from 'echarts/core'
import 'echarts/lib/chart/bar.js'
import 'echarts/lib/chart/line.js'
import 'echarts/lib/chart/pie.js'
import 'echarts/lib/component/title.js'
import 'echarts/lib/component/tooltip.js'
import 'echarts/lib/component/legend.js'
import 'echarts/lib/component/grid.js'
import 'echarts/lib/component/dataset.js'
import 'echarts/lib/renderer/installCanvasRenderer.js'

import { applyChartTheme } from './artifact.js'

/**
 * 在容器里挂载图表。
 *
 * @param {HTMLElement} el 已布局的容器（必须有宽高）
 * @param {object} option 归一化后的 ECharts option
 * @param {{dark?: boolean}} [options]
 * @returns {{setTheme: (dark: boolean) => void, dispose: () => void}}
 */
export function mountChart(el, option, { dark = false } = {}) {
  const chart = echarts.init(el, null, { renderer: 'canvas' })
  try {
    chart.setOption(applyChartTheme(option, dark), { notMerge: true })
  } catch (e) {
    // setOption 失败时释放已创建的实例与画布，避免残留一个空图表
    if (!chart.isDisposed()) chart.dispose()
    throw e
  }

  let observer = null
  let windowHandler = null
  const resize = () => {
    if (!chart.isDisposed()) chart.resize()
  }
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(resize)
    observer.observe(el)
  } else if (typeof window !== 'undefined') {
    windowHandler = resize
    window.addEventListener('resize', windowHandler)
  }

  return {
    setTheme(nextDark) {
      if (chart.isDisposed()) return
      chart.setOption(applyChartTheme(option, nextDark), { notMerge: true })
    },
    dispose() {
      if (observer) {
        observer.disconnect()
        observer = null
      }
      if (windowHandler) {
        window.removeEventListener('resize', windowHandler)
        windowHandler = null
      }
      if (!chart.isDisposed()) chart.dispose()
    },
  }
}
