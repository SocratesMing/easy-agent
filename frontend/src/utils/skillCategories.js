/**
 * 技能分类前端辅助。
 *
 * 类别本身由后端管理（`/agent/skill-center/categories`，用户级持久化）；技能的归属在
 * **技能创建时自动归类定稿**，之后不再变动，由列表 / 详情接口的 `category_id` +
 * `category_name` 给出，前端只做展示与筛选。这里只保留两件事：
 * 1. 虚拟兜底类别常量（后端也返回，这里用于首次渲染的占位）；
 * 2. 类别 id → SkillIcon 配色 key 的映射（自定义类别按 id 哈希取色，保证有区分度）。
 */

/** 虚拟兜底类别：既不是用户类别，也不能被删除 */
export const DEFAULT_CATEGORY = { id: 'default', name: '通用技能' }

/** SkillIcon 内置的配色 / 图标 key */
const ICON_KEYS = ['doc', 'code', 'design', 'data', 'api', 'default']

/** 类别 id → SkillIcon 配色 key */
export function iconCategoryFor(categoryId) {
  if (!categoryId) return 'default'
  if (ICON_KEYS.includes(categoryId)) return categoryId

  let hash = 0
  for (const ch of String(categoryId)) {
    hash = (hash * 31 + ch.codePointAt(0)) % 9973
  }
  return ICON_KEYS[hash % ICON_KEYS.length]
}
