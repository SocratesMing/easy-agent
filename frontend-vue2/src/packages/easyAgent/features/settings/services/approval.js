/** 权限设置（审批模式）：组件层不直连 api/，统一从这里出口（结构约束要求）。 */
export {
  getApprovalSettings,
  updateApprovalSettings,
} from '../../../api/settings.js'
