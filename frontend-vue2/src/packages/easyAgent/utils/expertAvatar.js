/**
 * 专家头像：取字 + 稳定配色。
 *
 * 背景：专家头像出现在四处——技能中心「专家」Tab 的卡片/详情、+ 号下拉专家列表、
 * 输入框专家胶囊、回答面板左上角标识。原先三处各写一套规则（下拉与面板按**列表
 * 序号**取色、消息标识与胶囊写死主题蓝），同一个专家四处不同色，而且序号取色会随
 * 搜索过滤、「还有 N 位，点击加载」而变。
 *
 * 现在统一为「按身份取色」：用 export_agent_id（消息侧字段名是 id）做 FNV-1a 哈希
 * 落到 8 档色板（.ea-0 ~ .ea-7，见 src/style.css）。同一专家在任意位置、刷新前后、
 * 换设备都是同一个颜色。
 *
 * 兼容两种数据结构：
 *   列表项 { export_agent_id, export_name_cn }（我的专家 / 公共专家列表）
 *   消息侧 { id, name }（后端 _expert_meta_for_message 写入用户消息）
 */

/** 色板档数，需与 style.css 里的 .ea-0 ~ .ea-7 保持一致 */
export const EXPERT_PALETTE_SIZE = 8

/** 取色种子：优先专家 ID（跨面板与消息一致），没有 ID 时退回中文名 */
function seedOf(expert) {
  if (!expert) return ''
  return String(
    expert.export_agent_id || expert.id || expert.export_name_cn || expert.name || ''
  ).trim()
}

/** 头像文字：专家中文名首字；取不到时用 ? 兜底 */
export function expertAvatarChar(expert) {
  const name = String((expert && (expert.export_name_cn || expert.name)) || '').trim()
  return name ? name.slice(0, 1) : '?'
}

/**
 * 稳定取色：FNV-1a 32 位哈希 + fmix32 终混，落到 8 档色板（同一字符串必得同一档，
 * 无需把颜色存库）。
 *
 * 为什么要有 fmix32：专家 ID 大多是长公共前缀（`preset_*_investment`），FNV-1a 的
 * 低位雪崩不足，直接 `% 8` 会扎堆——实测 5 个预置专家里 3 个落到同一档。加一步
 * murmur3 的 fmix32 终混后，5 个预置专家分别落在 5/7/6/3/0 档，互不撞色。
 *
 * 同一专家在不同位置传入的 ID 字段名不同（export_agent_id / id），但值相同，
 * 因此哈希结果也相同——这是四处同色的前提。
 */
export function expertColorIndex(expert) {
  const seed = seedOf(expert)
  if (!seed) return 0
  let hash = 0x811c9dc5
  for (let i = 0; i < seed.length; i += 1) {
    hash ^= seed.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  // fmix32（murmur3 的终混函数）：把变化扩散到低位，避免 % 8 扎堆
  hash ^= hash >>> 16
  hash = Math.imul(hash, 0x85ebca6b) >>> 0
  hash ^= hash >>> 13
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0
  hash ^= hash >>> 16
  return (hash >>> 0) % EXPERT_PALETTE_SIZE
}

/** 头像配色 class（.ea-N）；底色与字色由 style.css 的全局规则负责 */
export function expertAvatarClass(expert) {
  return `ea-${expertColorIndex(expert)}`
}
