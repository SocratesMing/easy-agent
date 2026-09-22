import request, { requestBlob } from '../utils/request.js'

export async function getPublicSkills() {
  return request(
    { url: '/agent/skill-center/public-skills', method: 'get' },
    '获取公共技能列表失败'
  )
}

export async function getUserSkills() {
  return request(
    { url: '/agent/skill-center/user-skills', method: 'get' },
    '获取用户技能列表失败'
  )
}

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

export async function importSkill(file) {
  const formData = new FormData()
  formData.append('file', file)
  return request({
    url: '/agent/skill-center/import-skill',
    method: 'post',
    data: formData,
  })
}
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
