// 分块渲染结果写进 DOM（MarkdownBody 用）。
//
// 不用 v-for + v-html：每块各包一层元素时，父组件里 `p:first-child { margin-top: 0 }`、
// `p:last-child { margin-bottom: 0 }` 这类结构选择器会对每一段都生效（display:contents 不改变
// DOM 结构），AI 回复的段落间距全部消失。这里直接把各块的节点按顺序放进组件根节点，
// DOM 结构与整篇 v-html 完全一样；内容（key + HTML）没变的块保留原节点，流式期间
// 选中的文字不丢、图片不重新加载。

/**
 * 按块更新容器的子节点。
 *
 * @param {Element|null} root 容器（组件根节点；其子节点完全由本函数管理）
 * @param {Array<{key: string, html: string, nodes: Node[]}>} prev 上一次的返回值（首次传 []）
 * @param {Array<{key: string, html: string}>} blocks renderMarkdownBlocks 的结果
 * @returns {Array<{key: string, html: string, nodes: Node[]}>} 本次结果，下次作为 prev 传入
 * @example
 * let rendered = []
 * rendered = patchMarkdownBlocks(el, rendered, renderMarkdownBlocks(md, renderer))
 */
export function patchMarkdownBlocks(root, prev, blocks) {
  if (!root) return prev || []
  const doc = root.ownerDocument
  const reusable = new Map()
  for (const item of prev || []) reusable.set(item.key + '\u0000' + item.html, item)

  const next = (blocks || []).map((block) => {
    const id = block.key + '\u0000' + block.html
    const kept = reusable.get(id)
    if (kept) {
      reusable.delete(id)
      return kept
    }
    const box = doc.createElement('div')
    box.innerHTML = block.html
    return { key: block.key, html: block.html, nodes: Array.from(box.childNodes) }
  })

  // 不再出现的旧块先移除
  for (const item of reusable.values()) {
    for (const node of item.nodes) {
      if (node.parentNode === root) root.removeChild(node)
    }
  }
  // 按顺序摆放：已经在正确位置的节点不动（保留选区），其余插到当前位置
  let cursor = root.firstChild
  for (const item of next) {
    for (const node of item.nodes) {
      if (node === cursor) cursor = cursor.nextSibling
      else root.insertBefore(node, cursor)
    }
  }
  // 不属于任何块的残留节点（正常不会有）一并清掉，保证容器内容与渲染结果一致
  while (cursor) {
    const after = cursor.nextSibling
    root.removeChild(cursor)
    cursor = after
  }
  return next
}
