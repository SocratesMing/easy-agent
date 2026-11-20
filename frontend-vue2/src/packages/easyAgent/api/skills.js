import request, { requestBlob } from '../utils/request.js'
import { createSkillsApi } from '../shared/api/skills.js'

export const {
  getPublicSkills,
  getUserSkills,
  addSkillToUser,
  removeSkillFromUser,
  importSkill,
  getSkillCategories,
  downloadSkill,
  enableSkill,
  disableSkill,
  getSkillDetail,
} = createSkillsApi(request, requestBlob)
