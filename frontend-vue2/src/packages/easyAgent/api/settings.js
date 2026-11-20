import request from '../utils/request.js'
import { createSettingsApi } from '../shared/api/settings.js'

export const {
  getMemory,
  updateMemory,
  getSystemPrompt,
  getVersionInfo,
  getSkills,
  getModels,
  getKnowledgeStatus,
  getWebSearchStatus,
  getUploadLimits,
  getApprovalSettings,
  updateApprovalSettings,
} = createSettingsApi(request)
