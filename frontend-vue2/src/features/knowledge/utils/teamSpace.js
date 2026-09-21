export function teamSpaceOptionSuffix(management = {}) {
  if (management.can_create) return ''
  if (management.is_admin) return '（暂无可用部门）'
  if (management.department_id) return '（需 admin 授权）'
  return '（需配置部门）'
}

export function teamSpaceEmptyHint(management = {}) {
  if (management.is_admin) return '请先在人员与部门中配置可用部门'
  if (management.department_id) return '由 admin 授权后可创建和管理'
  return '请先由 admin 配置账号所属部门'
}

export function defaultTeamDepartmentId(management = {}) {
  const departments = Array.isArray(management.departments)
    ? management.departments
    : []
  const current = String(management.department_id || '')
  if (current && departments.some(item => String(item.id) === current)) return current
  return departments.length ? String(departments[0].id || '') : current
}

export function canEditPermissionRole(base = {}, management = {}) {
  return base.visibility !== 'team' || Boolean(management.is_admin)
}

export function canRemovePermission(base = {}, management = {}, permission = {}) {
  return canEditPermissionRole(base, management) || permission.role === 'viewer'
}
