import request from '../utils/request.js'
import { createExpertApi } from '../shared/api/expert.js'

export const {
  addExpert,
  listExperts,
  listPublicExperts,
  getExpertMd,
  getSessionExperts,
  deleteExpert,
} = createExpertApi(request)
