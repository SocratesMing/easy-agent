import assert from 'node:assert/strict'
import test from 'node:test'

import { visualFrameUrl } from './mount.js'

test('iframe 地址带 kind，供框架页在 HTML 解析阶段预加载对应图表库', () => {
  assert.equal(
    visualFrameUrl('/agent/viz/frame.html', 'echarts'),
    '/agent/viz/frame.html?kind=echarts',
  )
  assert.equal(
    visualFrameUrl('/agent/viz/frame.html?theme=dark', 'mermaid'),
    '/agent/viz/frame.html?theme=dark&kind=mermaid',
  )
})
