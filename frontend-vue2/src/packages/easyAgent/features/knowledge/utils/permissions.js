// 权限管理的人员搜索：按显示名 / 账号匹配，空关键字不展示结果列表
export function filterPermissionUsers(users, query) {
  const keyword = String(query || '').trim().toLowerCase()
  if (!keyword) return []
  return (users || [])
    .filter(user => (
      String(user?.display_name || '').toLowerCase().includes(keyword)
      || String(user?.username || '').toLowerCase().includes(keyword)
    ))
    .sort((a, b) => String(a.display_name || '').localeCompare(
      String(b.display_name || ''),
      'zh-CN'
    ))
}
