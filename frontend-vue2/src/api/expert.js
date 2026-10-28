/**
 * 专家 Agent 接口封装
 *
 * 对应后端 easy_agent/api/expert.py：
 *   POST /agent/add                创建专家
 *   GET  /agent/list               我的专家列表
 *   GET  /agent/public-list        公共（预置）专家列表
 *   GET  /agent/expert-md          读取专家提示词 MD（只读预览）
 *   GET  /agent/session-experts    某会话里创建的专家（工作区只读条目）
 *   POST /agent/del                删除专家（默认软删）
 *
 * 专家对话不再有独立接口：选中专家后前端统一调 /agent/chat/stream 并带上
 * `expert_agent_id`（见 api/chat.js 的 sendMessage），提示词加载与相关性预检
 * 都在通用入口完成，SSE 事件格式与普通聊天完全一致。
 *
 * 新建与修改专家都改在对话框里由 Agent（expert-creator 技能）完成，故这里只
 * 保留浏览侧接口；后端的 POST /agent/update 仍存在，但当前无前端调用方。
 */
import request from '../utils/request.js'

/** 创建专家（分类标签 tag_id 由大模型自动生成，无需前端传入） */
export async function addExpert({ nameCn, prompt, sessionId = null, model = null }) {
  return request(
    {
      url: '/agent/add',
      method: 'post',
      data: {
        type: 'agent',
        name_cn: nameCn,
        prompt,
        session_id: sessionId,
        model,
      },
    },
    '创建专家失败'
  )
}

/** 我的专家列表 */
export async function listExperts({
  keyword = '',
  status = '',
  page = 1,
  pageSize = 20,
  includeDisabled = false,
} = {}) {
  return request(
    {
      url: '/agent/list',
      method: 'get',
      params: {
        keyword,
        status,
        page,
        page_size: pageSize,
        include_disabled: includeDisabled,
      },
    },
    '获取专家列表失败'
  )
}

/**
 * 公共（预置）专家列表：随项目内置的专家（`exports/` 目录），只读，
 * 不区分用户。字段与 listExperts 的 items 一致（另带 `scope: 'public'`）。
 */
export async function listPublicExperts() {
  return request(
    {
      url: '/agent/public-list',
      method: 'get',
    },
    '获取公共专家列表失败'
  )
}

/**
 * 读取专家提示词 MD（只读）：详情弹窗与工作区预览共用。
 * 返回 `{ expert_agent_id, scope, file_name, content, version }`。
 */
export async function getExpertMd(expertId) {
  return request(
    {
      url: '/agent/expert-md',
      method: 'get',
      params: { expert_id: expertId },
    },
    '获取专家提示词失败'
  )
}

/**
 * 某会话里创建的专家（工作区只读条目的数据源）。
 * 返回 `{ items: [{ expert_agent_id, name_cn, name_en, version }] }`。
 */
export async function getSessionExperts(sessionId) {
  if (!sessionId) return { items: [] }
  return request(
    {
      url: '/agent/session-experts',
      method: 'get',
      params: { session_id: sessionId },
    },
    '获取会话内专家失败'
  )
}

/** 删除专家（hard=true 时物理删除） */
export async function deleteExpert(exportAgentId, hard = false) {
  return request(
    {
      url: '/agent/del',
      method: 'post',
      data: { export_agent_id: exportAgentId, hard },
    },
    '删除专家失败'
  )
}
