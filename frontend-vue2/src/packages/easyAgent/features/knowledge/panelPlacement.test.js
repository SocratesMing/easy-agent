import assert from 'node:assert/strict'
import test from 'node:test'

import { PANEL_GAP, VIEWPORT_MARGIN, placeScopePanel } from './panelPlacement.js'

// AI 侧边栏贴着屏幕右边：「知识库」菜单项（锚点）在视口右侧、靠近底部的输入框上方
const sidebarAnchor = { top: 450, bottom: 491, left: 1025, right: 1199 }
const viewport = { viewportWidth: 1440, viewportHeight: 700 }
const rowInside = (place, height, anchor) => {
  const middle = (anchor.top + anchor.bottom) / 2
  return place.top <= middle && middle <= place.top + height
}

test('知识库少（面板矮）：顶边与「知识库」菜单项齐平，不再悬在菜单项上方', () => {
  // 2 个知识库时面板实测 174px。旧算法按估计的 420px 上移，得到 top=272、底边 446，
  // 比菜单项顶边（450）还高，鼠标横移进不去
  const place = placeScopePanel({ anchor: sidebarAnchor, panelHeight: 174, ...viewport })
  assert.equal(place.top, sidebarAnchor.top)
  assert.ok(rowInside(place, 174, sidebarAnchor))
})

test('知识库多（底部放不下）：整体上移到贴住视口底边，菜单项那一行仍在面板范围内', () => {
  const place = placeScopePanel({ anchor: sidebarAnchor, panelHeight: 358, ...viewport })
  assert.equal(place.top + 358, viewport.viewportHeight - VIEWPORT_MARGIN)
  assert.ok(rowInside(place, 358, sidebarAnchor))
})

test('任意高度下菜单项那一行都在面板竖直范围内，且面板不出视口', () => {
  for (let height = 60; height <= 680; height += 20) {
    const place = placeScopePanel({ anchor: sidebarAnchor, panelHeight: height, ...viewport })
    assert.ok(rowInside(place, height, sidebarAnchor), `height=${height} top=${place.top}`)
    assert.ok(place.top >= VIEWPORT_MARGIN && place.top + height <= viewport.viewportHeight - VIEWPORT_MARGIN, `height=${height}`)
  }
})

test('面板还没渲染（高度按 0）时先把顶边对齐锚点', () => {
  assert.equal(placeScopePanel({ anchor: sidebarAnchor, panelHeight: 0, ...viewport }).top, sidebarAnchor.top)
})

test('比视口还高的面板贴住上方留白', () => {
  assert.equal(placeScopePanel({ anchor: sidebarAnchor, panelHeight: 900, ...viewport }).top, VIEWPORT_MARGIN)
})

test('水平：右侧放得下放右侧，放不下（侧边栏）放左侧，都与锚点隔 PANEL_GAP', () => {
  const right = placeScopePanel({ anchor: { top: 300, left: 40, right: 220 }, panelHeight: 200, ...viewport })
  assert.equal(right.side, 'right')
  assert.equal(right.left, 220 + PANEL_GAP)

  const left = placeScopePanel({ anchor: sidebarAnchor, panelHeight: 200, ...viewport })
  assert.equal(left.side, 'left')
  assert.equal(left.left + left.width, sidebarAnchor.left - PANEL_GAP)
})

test('窄视口：宽度不超过 90vw，左侧放不下时贴住左边留白', () => {
  const place = placeScopePanel({ anchor: { top: 300, left: 20, right: 200 }, panelHeight: 200, viewportWidth: 400, viewportHeight: 700 })
  assert.equal(place.width, 360)
  assert.equal(place.side, 'left')
  assert.equal(place.left, VIEWPORT_MARGIN)
})
