import request, { requestBlob } from '@/utils/request'

/**
 * 获取技能中心的公共技能列表
 *
 * @returns {Promise<Array>} 公共技能列表
 * @example
 * getPublicSkills()
 */
export async function getPublicSkills() {
  return request(
    {
      url: '/agent/skill-center/public-skills',
      method: 'get',
    },
    '获取公共技能列表失败'
  )
}

/**
 * 获取当前用户已添加的技能列表
 *
 * @returns {Promise<Array>} 用户技能列表
 * @example
 * getUserSkills()
 */
export async function getUserSkills() {
  return request(
    {
      url: '/agent/skill-center/user-skills',
      method: 'get',
    },
    '获取用户技能列表失败'
  )
}

/**
 * 将公共技能添加到当前用户
 *
 * @param {string} dirName 技能目录名
 * @returns {Promise<Object>} 添加结果
 * @example
 * addSkillToUser('pdf-helper')
 */
export async function addSkillToUser(dirName) {
  return request(
    {
      url: '/agent/skill-center/add-skill',
      method: 'post',
      data: { dir_name: dirName },
    },
    '添加技能失败'
  )
}

/**
 * 从当前用户移除技能
 *
 * @param {string} dirName 技能目录名
 * @returns {Promise<Object>} 移除结果
 * @example
 * removeSkillFromUser('pdf-helper')
 */
export async function removeSkillFromUser(dirName) {
  return request(
    {
      url: '/agent/skill-center/remove-skill',
      method: 'post',
      data: { dir_name: dirName },
    },
    '移除技能失败'
  )
}

/**
 * 导入本地技能包（zip）
 *
 * @param {File} file 技能压缩包
 * @returns {Promise<Object>} 导入结果
 * @example
 * importSkill(file)
 */
export async function importSkill(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/agent/skill-center/import-skill',
    method: 'post',
    data: formData,
  })
}

/**
 * 下载指定技能（zip）
 *
 * @param {string} dirName 技能目录名
 * @returns {Promise<void>} 无返回值
 * @example
 * downloadSkill('pdf-helper')
 */
export async function downloadSkill(dirName) {
  const blob = await requestBlob({
    url: `/agent/skill-center/download-skill/${encodeURIComponent(dirName)}`,
    method: 'get',
  })
  const blobUrl = window.URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = blobUrl
  link.download = `${dirName}.zip`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  window.URL.revokeObjectURL(blobUrl)
}
