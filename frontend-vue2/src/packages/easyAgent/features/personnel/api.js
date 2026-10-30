import request, { requestRaw } from '../../utils/request.js'
import { buildPersonnelFilters, filenameFromContentDisposition } from './model.js'

const ROOT = '/api/personnel'
export const getLoginPolicy = () => request({ url: `${ROOT}/login-policy`, method: 'get' })
const KNOWLEDGE_ADMIN_ROOT = '/api/knowledge/v1/admin'

export function listPersonnelUsers(filters = {}) {
  return request({
    url: `${ROOT}/users`,
    method: 'get',
    params: buildPersonnelFilters(filters),
  }, '获取人员列表失败')
}

export function createPersonnelUser(payload) {
  return request({
    url: `${ROOT}/users`,
    method: 'post',
    data: payload,
  }, '新增人员失败')
}

export function updatePersonnelUser(userId, payload) {
  return request({
    url: `${ROOT}/users/${encodeURIComponent(userId)}`,
    method: 'put',
    data: payload,
  }, '更新人员失败')
}

export function deletePersonnelUser(userId) {
  return request({
    url: `${ROOT}/users/${encodeURIComponent(userId)}`,
    method: 'delete',
  }, '删除人员失败')
}

export function importPersonnelUsers(file, source) {
  const form = new FormData()
  form.append('file', file)
  form.append('source', source)
  return request({
    url: `${ROOT}/import`,
    method: 'post',
    data: form,
  }, '导入人员信息失败')
}

export async function getPersonnelImportTemplate() {
  try {
    const response = await requestRaw({
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
  return request({
    url: `${KNOWLEDGE_ADMIN_ROOT}/team-space-managers`,
    method: 'get',
  }, '获取公共空间权限失败')
}

export function setTeamSpaceManager(userId, enabled) {
  return request({
    url: `${KNOWLEDGE_ADMIN_ROOT}/team-space-managers/${encodeURIComponent(userId)}`,
    method: 'put',
    data: { enabled: Boolean(enabled) },
  }, '设置公共空间权限失败')
}
