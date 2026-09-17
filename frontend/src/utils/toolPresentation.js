/**
 * 工具卡片呈现：把 (工具名, 参数, 结果) 映射为 dsh 风格的 card 渲染模型。
 *
 * 纯函数，实时流与历史回放共用。前端派生 dsh 的 presentCall/presentResult：
 *   execute → terminal   read_file → read   write_file/edit_file → diff
 *   ls/glob → search(paths)   grep → search(matches)   其它 → generic
 */
function oneLine(s) {
  return String(s == null ? '' : s).split('\n')[0]
}
function short(s, n = 64) {
  s = String(s == null ? '' : s)
  return s.length > n ? s.slice(0, n) + '…' : s
}
function baseName(p) {
  return String(p || '').split('/').pop()
}
// 解析 ls/glob 的结果：JSON 数组或 Python 列表 repr（"['a', 'b']"）。
function parsePathList(text) {
  const t = String(text || '').trim()
  if (!t || t === '[]') return []
  try {
    const j = JSON.parse(t)
    if (Array.isArray(j)) return j.map(String)
  } catch (_) { /* 非 JSON，按 Python repr 处理 */ }
  return t.replace(/^\[|\]$/g, '')
    .split(/',\s*'/)
    .map(s => s.replace(/^['"]|['"]$/g, '').trim())
    .filter(Boolean)
}

export function toolCard(toolName, args, result, success = true) {
  const a = args && typeof args === 'object' ? args : {}
  const r = typeof result === 'string'
    ? result
    : (result == null ? '' : JSON.stringify(result))

  switch (toolName) {
    case 'execute': {
      const m = /\[Command (?:succeeded|failed) with exit code (-?\d+)\]/.exec(r)
      const code = m ? Number(m[1]) : null
      const body = r.replace(/\[Command (?:succeeded|failed) with exit code -?\d+\]\s*$/m, '').trimEnd()
      return {
        card: 'terminal', kind: 'execute',
        title: oneLine(a.command) || '执行命令',
        meta: code != null ? `exit ${code}` : '',
        error: success === false || (code != null && code !== 0),
        body,
      }
    }
    case 'read_file':
      return {
        card: 'read', kind: 'read',
        title: baseName(a.file_path || a.path) || '读取文件',
        meta: short(a.file_path || a.path, 60),
        error: success === false,
        body: r,
      }
    case 'write_file': {
      const path = a.file_path || a.path || ''
      return {
        card: 'diff', kind: 'edit', title: `写入 ${baseName(path)}`, meta: short(path, 60),
        error: success === false,
        diffs: [{ path, lines: String(a.content ?? '').split('\n').map(t => ({ type: 'add', text: t })) }],
      }
    }
    case 'edit_file': {
      const path = a.file_path || a.path || ''
      const lines = []
      for (const t of String(a.old_string ?? '').split('\n')) lines.push({ type: 'del', text: t })
      for (const t of String(a.new_string ?? '').split('\n')) lines.push({ type: 'add', text: t })
      return {
        card: 'diff', kind: 'edit', title: `编辑 ${baseName(path)}`, meta: short(path, 60),
        error: success === false,
        diffs: [{ path, lines }],
      }
    }
    case 'ls':
    case 'glob': {
      const paths = parsePathList(r)
      return {
        card: 'search', shape: 'paths', kind: 'search',
        title: toolName === 'glob' ? (a.pattern || '*') : (a.path || '.'),
        meta: `${paths.length} 项`, error: success === false, paths,
      }
    }
    case 'grep':
      return {
        card: 'search', shape: 'matches', kind: 'search',
        title: a.pattern || 'grep', meta: short(a.path || '', 60),
        error: success === false, raw: r,
      }
    default:
      return {
        card: 'generic', kind: 'other', title: toolName || 'tool',
        meta: short(JSON.stringify(a || {}), 56),
        error: success === false, args: a, body: r,
      }
  }
}
