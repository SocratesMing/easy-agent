/**
 * 知识库面板（KnowledgeScopeSelector）的弹出位置。纯函数，单测见 panelPlacement.test.js。
 *
 * - 水平：锚点右侧放得下就放右侧，否则放左侧（AI 侧边栏贴着屏幕右边，通常在左侧）。
 * - 竖直：顶边与锚点顶边齐平；底部放不下就整体上移到正好贴住视口底边。
 *
 * panelHeight 必须是面板的实测高度（列表异步加载、条数不定）。以前按估计的 420px 上移，
 * 知识库只有一两个时面板只有一两百像素高，整块悬在「知识库」菜单项上方：鼠标横移进不去，
 * 斜着移又会扫过「添加文件」把面板切走。按实测高度算，锚点那一行总在面板的竖直范围内，
 * 从锚点横着移过去就进了面板。
 */

// 面板宽度（与 CSS 的 width:380px / max-width:90vw 同源）
export const PANEL_WIDTH = 380
// 与锚点的水平间距，同「+」技能子面板（ChatInput 的 SUBPANEL_GAP）
export const PANEL_GAP = 6
// 离视口边缘的最小留白
export const VIEWPORT_MARGIN = 8

/**
 * @param {object} options
 * @param {{ top: number, left: number, right: number }} options.anchor 锚点的 getBoundingClientRect()
 * @param {number} options.panelHeight 面板实测高度；还没渲染时传 0（顶边先对齐锚点）
 * @param {number} options.viewportWidth
 * @param {number} options.viewportHeight
 * @returns {{ top: number, left: number, width: number, side: 'right' | 'left' }}
 */
export function placeScopePanel({ anchor, panelHeight = 0, viewportWidth, viewportHeight }) {
  const width = Math.min(PANEL_WIDTH, Math.floor(viewportWidth * 0.9))
  const fitsRight = anchor.right + PANEL_GAP + width <= viewportWidth - VIEWPORT_MARGIN
  const left = fitsRight
    ? anchor.right + PANEL_GAP
    : Math.max(VIEWPORT_MARGIN, anchor.left - PANEL_GAP - width)
  const top = Math.max(
    VIEWPORT_MARGIN,
    Math.min(anchor.top, viewportHeight - VIEWPORT_MARGIN - panelHeight)
  )
  return { top, left, width, side: fitsRight ? 'right' : 'left' }
}
