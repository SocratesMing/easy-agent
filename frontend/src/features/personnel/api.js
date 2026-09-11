import { request, requestJson } from '../../api/request.js'
import { buildPersonnelFilters, filenameFromContentDisposition } from './model.js'

const ROOT = '/api/personnel'
const KNOWLEDGE_ADMIN_ROOT = '/api/knowledge/v1/admin'

export function listPersonnelUsers(filters = {}) {
  return requestJson({
    url: `${ROOT}/users`,
    method: 'get',
    params: buildPersonnelFilters(filters),
  }, '获取人员列表失败')
}

export function createPersonnelUser(payload) {
  return requestJson({
    url: `${ROOT}/users`,
    method: 'post',
    data: payload,
  }, '新增人员失败')
}

export function updatePersonnelUser(userId, payload) {
  return requestJson({
    url: `${ROOT}/users/${encodeURIComponent(userId)}`,
    method: 'put',
    data: payload,
  }, '更新人员失败')
}

export function importPersonnelUsers(file, source) {
  const form = new FormData()
  form.append('file', file)
  form.append('source', source)
  return requestJson({
    url: `${ROOT}/import`,
    method: 'post',
    data: form,
  }, '导入人员信息失败')
}

export async function getPersonnelImportTemplate() {
  try {
    const response = await request({
      url: `${ROOT}/import-template`,
      method: 'get',
      responseType: 'blob',
    })
    return {
      blob: response.data,
      filename: filenameFromContentDisposition(response.headers && response.headers['content-disposition']),
    }
  } catch (error) {
    error.message = '下载导入模板失败'
    throw error
  }
}

export function listTeamSpaceManagers() {
  return requestJson({
    url: `${KNOWLEDGE_ADMIN_ROOT}/team-space-managers`,
    method: 'get',
  }, '获取团队空间权限失败')
}

export function setTeamSpaceManager(userId, enabled) {
  return requestJson({
    url: `${KNOWLEDGE_ADMIN_ROOT}/team-space-managers/${encodeURIComponent(userId)}`,
    method: 'put',
    data: { enabled: Boolean(enabled) },
  }, '设置团队空间权限失败')
}
