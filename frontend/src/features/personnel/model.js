const ACTIVE_VALUES = new Set(['active', '启用', '正常', '1', 'true'])
const DISABLED_VALUES = new Set(['disabled', '停用', '禁用', '0', 'false'])

export const PERSONNEL_STATUS_OPTIONS = [
  { value: 'active', label: '启用' },
  { value: 'disabled', label: '停用' },
]

export function normalizeAccountStatus(value) {
  const normalized = String(value == null ? '' : value).trim().toLowerCase()
  if (DISABLED_VALUES.has(normalized)) return 'disabled'
  if (ACTIVE_VALUES.has(normalized)) return 'active'
  return normalized || 'active'
}

export function normalizePersonnelUser(user = {}) {
  return {
    ...user,
    user_id: String(user.user_id || user.id || ''),
    username: String(user.username || user.account || ''),
    display_name: String(user.display_name || user.name || ''),
    employee_id: String(user.employee_id || ''),
    department_id: String(user.department_id || user.organization_id || ''),
    department_name: String(user.department_name || ''),
    email: String(user.email || ''),
    position: String(user.position || ''),
    mobile: String(user.mobile || user.phone || ''),
    account_status: normalizeAccountStatus(user.account_status || user.status),
    personnel_source: String(user.personnel_source || user.source || ''),
  }
}

export function normalizePersonnelPage(payload) {
  const rawItems = Array.isArray(payload)
    ? payload
    : (payload && (payload.items || payload.users)) || []
  const items = Array.isArray(rawItems) ? rawItems.map(normalizePersonnelUser) : []
  const totalValue = payload && !Array.isArray(payload)
    ? (payload.total != null ? payload.total : payload.count)
    : null
  const total = totalValue == null ? items.length : Number(totalValue)
  return {
    items,
    total: Number.isFinite(total) ? total : items.length,
    page: Number(payload && payload.page) || 1,
    page_size: Number(payload && payload.page_size) || Math.max(items.length, 1),
  }
}

export function emptyPersonnelForm() {
  return {
    username: '',
    display_name: '',
    employee_id: '',
    department_id: '',
    department_name: '',
    email: '',
    position: '',
    mobile: '',
    account_status: 'active',
    personnel_source: '',
  }
}

function trimmed(value) {
  return String(value == null ? '' : value).trim()
}

export function buildPersonnelPayload(form) {
  return {
    username: trimmed(form.username),
    display_name: trimmed(form.display_name),
    employee_id: trimmed(form.employee_id),
    department_id: trimmed(form.department_id),
    department_name: trimmed(form.department_name),
    email: trimmed(form.email),
    position: trimmed(form.position),
    mobile: trimmed(form.mobile),
    account_status: normalizeAccountStatus(form.account_status),
    source: trimmed(form.personnel_source || form.source),
  }
}

export function validatePersonnelForm(form) {
  const payload = buildPersonnelPayload(form)
  const errors = {}
  if (!payload.username) errors.username = '请填写账号'
  else if (!/^[A-Za-z0-9_-]{3,64}$/.test(payload.username)) {
    errors.username = '账号仅支持 3-64 位字母、数字、下划线或连字符'
  }
  if (!payload.display_name) errors.display_name = '请填写姓名'
  if (!payload.department_id) errors.department_id = '请填写部门编号'
  if (!payload.department_name) errors.department_name = '请填写部门名称'
  if (!payload.source) errors.personnel_source = '请填写人员信息来源'
  if (payload.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(payload.email)) {
    errors.email = '邮箱格式不正确'
  }
  if (!PERSONNEL_STATUS_OPTIONS.some(item => item.value === payload.account_status)) {
    errors.account_status = '账号状态只能是启用或停用'
  }
  return errors
}

export function buildPersonnelFilters({
  keyword = '',
  accountStatus = '',
  departmentId = '',
  page = 1,
  pageSize = 50,
} = {}) {
  const filters = {}
  if (trimmed(keyword)) filters.keyword = trimmed(keyword)
  if (trimmed(accountStatus)) filters.account_status = trimmed(accountStatus)
  if (trimmed(departmentId)) filters.department_id = trimmed(departmentId)
  filters.page = Math.max(1, Number(page) || 1)
  filters.page_size = Math.min(200, Math.max(1, Number(pageSize) || 50))
  return filters
}

export function filenameFromContentDisposition(value, fallback = '人员信息导入模板.xlsx') {
  const header = String(value || '')
  const utf8Match = header.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match) {
    try {
      return decodeURIComponent(utf8Match[1].replace(/^['"]|['"]$/g, ''))
    } catch (_) {
      return utf8Match[1]
    }
  }
  const plainMatch = header.match(/filename=["']?([^;"']+)/i)
  return plainMatch ? plainMatch[1].trim() : fallback
}

export function summarizeImportResult(result = {}) {
  const created = Number(result.created != null ? result.created : result.created_count)
  const updated = Number(result.updated != null ? result.updated : result.updated_count)
  const skipped = Number(result.skipped != null ? result.skipped : result.skipped_count)
  const safeCreated = Number.isFinite(created) ? created : 0
  const safeUpdated = Number.isFinite(updated) ? updated : 0
  const safeSkipped = Number.isFinite(skipped) ? skipped : 0
  return `导入完成：新增 ${safeCreated} 人，更新 ${safeUpdated} 人，跳过 ${safeSkipped} 人`
}

export function updateTeamManagerIds(currentIds, userId, enabled) {
  const next = new Set(currentIds || [])
  const normalizedId = String(userId || '')
  if (!normalizedId) return next
  if (enabled) next.add(normalizedId)
  else next.delete(normalizedId)
  return next
}

export function formatPersonnelValidationDetail(detail, maxErrors = 8) {
  if (!detail || typeof detail.message !== 'string') return ''
  const errors = Array.isArray(detail.errors)
    ? detail.errors.map(item => String(item)).filter(Boolean)
    : []
  if (!errors.length) return detail.message

  const visibleLimit = Math.max(1, Number(maxErrors) || 8)
  const visibleErrors = errors.slice(0, visibleLimit)
  const hiddenCount = errors.length - visibleErrors.length
  const countHint = hiddenCount > 0
    ? `（共 ${errors.length} 条，显示前 ${visibleErrors.length} 条）`
    : ''
  const hiddenHint = hiddenCount > 0 ? `；另有 ${hiddenCount} 条未显示` : ''
  return `${detail.message}${countHint}：${visibleErrors.join('；')}${hiddenHint}`
}
