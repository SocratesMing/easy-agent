/**
 * 专家 Agent 接口封装（Vue3 / vite 版）
 *
 * 对应后端 easy_agent/api/expert.py：
 *   POST /agent/add                创建专家
 *   GET  /agent/list               我的专家列表
 *   GET  /agent/public-list        公共（预置）专家列表（只读目录 experts/）
 *   GET  /agent/expert-md          读取专家提示词 MD（只读预览）
 *   GET  /agent/session-experts    某会话里创建的专家（工作区只读条目）
 *   POST /agent/del                删除专家（默认软删）
 *
 * 与 api/chat.js 保持同一套请求约定（API_BASE_URL + authFetch）。
 *
 * 说明：专家对话**没有**独立接口——选中的专家随 /agent/chat/stream 的
 * `expert_agent_id` 字段上行（见 api/chat.js::sendMessage），因此本文件只有浏览侧接口。
 * 新建与修改专家都改在对话框里由 Agent（expert-creator 技能）完成；后端的
 * POST /agent/update 仍存在，但当前无前端调用方。
 */
import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

function generateMessageId() {
  return `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`
}

async function _errorOf(response, fallback) {
  const data = await response.json().catch(() => ({}))
  return new Error(data.detail || fallback)
}

/** 创建专家（分类标签 tag_id 由大模型自动生成，无需前端传入） */
export async function addExpert({ nameCn, prompt, sessionId = null, model = null }) {
  const response = await authFetch(`${API_BASE_URL}/agent/add`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      type: 'agent',
      name_cn: nameCn,
      prompt,
      session_id: sessionId,
      model,
    }),
  })
  if (!response.ok) throw await _errorOf(response, '创建专家失败')
  return await response.json()
}

/** 我的专家列表 */
export async function listExperts({
  keyword = '',
  status = '',
  page = 1,
  pageSize = 20,
  includeDisabled = false,
} = {}) {
  const params = new URLSearchParams({
    keyword,
    status,
    page: String(page),
    page_size: String(pageSize),
    include_disabled: String(includeDisabled),
  })
  const response = await authFetch(`${API_BASE_URL}/agent/list?${params.toString()}`)
  if (!response.ok) throw await _errorOf(response, '获取专家列表失败')
  return await response.json()
}

/**
 * 公共（预置）专家列表：随项目内置的专家（`experts/` 目录），只读，
 * 不区分用户。字段与 listExperts 的 items 一致（另带 `scope: 'public'`）。
 */
export async function listPublicExperts() {
  const response = await authFetch(`${API_BASE_URL}/agent/public-list`)
  if (!response.ok) throw await _errorOf(response, '获取公共专家列表失败')
  return await response.json()
}

/**
 * 读取专家提示词 MD（只读）：详情弹窗与工作区预览共用。
 * 返回 `{ expert_agent_id, scope, file_name, content, version }`。
 */
export async function getExpertMd(expertId) {
  const params = new URLSearchParams({ expert_id: expertId })
  const response = await authFetch(`${API_BASE_URL}/agent/expert-md?${params.toString()}`)
  if (!response.ok) throw await _errorOf(response, '获取专家提示词失败')
  return await response.json()
}

/**
 * 某会话里创建的专家（工作区只读条目的数据源）。
 * 返回 `{ items: [{ expert_agent_id, name_cn, name_en, version }] }`。
 */
export async function getSessionExperts(sessionId) {
  if (!sessionId) return { items: [] }
  const params = new URLSearchParams({ session_id: sessionId })
  const response = await authFetch(
    `${API_BASE_URL}/agent/session-experts?${params.toString()}`
  )
  if (!response.ok) throw await _errorOf(response, '获取会话内专家失败')
  return await response.json()
}

/** 删除专家（hard=true 时物理删除） */
export async function deleteExpert(exportAgentId, hard = false) {
  const response = await authFetch(`${API_BASE_URL}/agent/del`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ export_agent_id: exportAgentId, hard }),
  })
  if (!response.ok) throw await _errorOf(response, '删除专家失败')
  return await response.json()
}
