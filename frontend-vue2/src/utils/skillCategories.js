/**
 * 技能分类：按技能目录名映射到固定 5 类（+ 通用兜底）。
 *
 * 沿用改造前 SkillListPanel 里的映射表，只是抽出来共用；
 * 分类只用于「分类 chips 筛选」与图标配色，后端不做分类字段。
 */

export const CATEGORIES = [
  { key: 'all', label: '全部' },
  { key: 'doc', label: '文档处理' },
  { key: 'code', label: '代码开发' },
  { key: 'design', label: '设计创意' },
  { key: 'data', label: '数据分析' },
  { key: 'api', label: 'API集成' },
  { key: 'default', label: '通用技能' },
]

const CATEGORY_PRESETS = {
  doc: ['pdf', 'docx', 'pptx', 'xlsx', 'doc-coauthoring'],
  code: ['mcp-builder', 'skill-creator', 'claude-api'],
  design: [
    'frontend-design',
    'canvas-design',
    'theme-factory',
    'brand-guidelines',
    'algorithmic-art',
    'web-artifacts-builder',
  ],
  data: ['webapp-testing', 'strategy_fx'],
  api: ['internal-comms', 'slack-gif-creator'],
}

const LABELS = Object.fromEntries(CATEGORIES.map((item) => [item.key, item.label]))

/** 目录名 → 分类 key（未命中返回 default） */
export function getSkillCategory(skill) {
  const dirName = typeof skill === 'string' ? skill : skill?.dir_name
  if (!dirName) return 'default'
  for (const [key, presets] of Object.entries(CATEGORY_PRESETS)) {
    if (presets.includes(dirName)) return key
  }
  return 'default'
}

/** 目录名 → 中文分类名 */
export function getCategoryLabel(skill) {
  return LABELS[getSkillCategory(skill)] || LABELS.default
}
