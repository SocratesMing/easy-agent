/**
 * 专家 Agent 接口封装
 *
 * 对应后端 easy_agent/api/expert.py：
 *   POST /agent/add                创建专家
 *   POST /agent/update             修改专家（新版本目录）
 *   GET  /agent/list               我的专家列表
 *   GET  /agent/public-list        公共（预置）专家列表
 *   POST /agent/del                删除专家（默认软删）
 *
 * 专家对话不再有独立接口：选中专家后前端统一调 /agent/chat/stream 并带上
 * `expert_agent_id`（见 api/chat.js 的 sendMessage），提示词加载与相关性预检
 * 都在通用入口完成，SSE 事件格式与普通聊天完全一致。
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

/** 修改专家 */
export async function updateExpert({
  exportAgentId,
  nameCn = null,
  prompt = null,
  model = null,
}) {
  return request(
    {
      url: '/agent/update',
      method: 'post',
      data: {
        export_agent_id: exportAgentId,
        name_cn: nameCn,
        prompt,
        modify_type: 'edit',
        model,
      },
    },
    '修改专家失败'
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
