/**
 * 技能目录共享状态：目录名 → 技能信息（名称 / 图标 / 分类 / 描述）。
 *
 * 两处都要按目录名反查技能：
 * 1. 对话气泡：把结构化的 `message.skills`（技能目录名）渲染成技能标签，标签的展示名
 *    与图标从这里反查；老数据（前缀拼在正文里的消息）也靠它判断 `/xxx` 是不是技能；
 * 2. 会话列表 / 侧边栏历史：老会话标题存的是首条消息原文，同样带着这串前缀。
 *
 * 用共享响应式对象让「加载完成后所有用到的地方一起刷新」，同时模块内做 TTL 缓存 +
 * 并发去重：同一次会话里多个气泡同时挂载也只打一次接口。
 */
import { reactive } from 'vue'

import { getUserSkills } from '../../../api/skills.js'
import { stripSkillPrefix } from '../../../utils/skillTokens.js'

/** 缓存有效期：技能列表变更后最多 60s 内生效（与输入框技能选择面板的复用窗口一致） */
const DEFAULT_TTL = 60000

export const skillDirectoryStore = reactive({
  /** 目录名 → 技能信息（getUserSkills 的原始条目） */
  byDirName: {},
  /** 目录名列表（解析时按它判断 /xxx 是不是技能；空数组表示目录尚未加载） */
  names: [],
  loaded: false,
})

let loadedAt = 0
let inflight = null

/**
 * 确保技能目录已加载（命中缓存直接返回）。
 *
 * @param {{ force?: boolean, ttl?: number }} [options] force 跳过缓存强制刷新
 * @returns {Promise<typeof skillDirectoryStore>} 接口失败时保持空目录，调用方按「无技能目录」降级
 */
export function ensureSkillDirectoryLoaded(options) {
  const { force = false, ttl = DEFAULT_TTL } = options || {}
  if (!force && skillDirectoryStore.loaded && Date.now() - loadedAt < ttl) {
    return Promise.resolve(skillDirectoryStore)
  }
  if (inflight) return inflight

  inflight = getUserSkills()
    .then((data) => {
      const items = data && Array.isArray(data.skills) ? data.skills : []
      const byDirName = {}
      items.forEach((skill) => {
        if (skill && skill.dir_name) byDirName[skill.dir_name] = skill
      })
      skillDirectoryStore.byDirName = byDirName
      skillDirectoryStore.names = Object.keys(byDirName)
      skillDirectoryStore.loaded = true
      loadedAt = Date.now()
      return skillDirectoryStore
    })
    .catch((error) => {
      console.error('获取技能目录失败:', error)
      return skillDirectoryStore
    })
    .then((result) => {
      inflight = null
      return result
    })

  return inflight
}

/**
 * 会话标题去掉旧前端拼接的 `/技能名` 前缀。
 *
 * 老会话的标题是首条消息原文（形如 `/demo-skill 测试对话`），侧边栏直接显示很丑；
 * 这里按技能目录反查还原成用户真正输入的内容。标题只由技能构成（被截断）时改用
 * 技能展示名兜底。目录尚未加载或目录名不匹配时原样返回，绝不误删路径开头。
 *
 * @param {string} title 会话标题原文
 * @returns {string} 展示用标题
 */
export function skillFreeTitle(title) {
  return stripSkillPrefix(title, skillDirectoryStore.names, skillDirectoryStore.byDirName)
}
