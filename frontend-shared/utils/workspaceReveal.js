// 会话开始时记下工作区文件，结束后只在出现新文件或内容变化时展开。
// 只看根目录会漏掉写进已有子目录的产物，所以按目录往下走几层。

const WALK_DEPTH = 5
export const WORKSPACE_REVEAL_DELAY_MS = 300

export async function collectWorkspaceFiles(listDir) {
  const files = new Map()
  async function walk(dir, depth) {
    const response = await listDir(dir)
    const items = response && response.items ? response.items : []
    for (const item of items) {
      if (item.type === 'directory') {
        if (depth < WALK_DEPTH && item.path) await walk(item.path, depth + 1)
      } else if (item.type === 'file' && item.path) {
        files.set(item.path, item.size || 0)
      }
    }
  }
  await walk('', 0)
  return files
}

export function workspaceHasNewFiles(before, after) {
  if (!before || !after) return false
  for (const [path, size] of after) {
    if (!before.has(path) || before.get(path) !== size) return true
  }
  return false
}

export function createWorkspaceReveal(listDir, { delay } = {}) {
  const wait = delay || (ms => new Promise(resolve => setTimeout(resolve, ms)))
  let generation = 0
  let pending = null

  return {
    remember(sessionId) {
      if (!sessionId) {
        pending = null
        return
      }
      const current = ++generation
      pending = collectWorkspaceFiles(path => listDir(path, sessionId)).then(
        files => (generation === current ? files : null),
        () => null,
      )
    },
    async reveal({ sessionId, stillCurrent, refresh, onExpand }) {
      const before = pending
      pending = null
      await wait(WORKSPACE_REVEAL_DELAY_MS)
      if (!stillCurrent()) return
      await refresh()
      if (!stillCurrent()) return
      const baseline = before ? await before : null
      if (!baseline || !stillCurrent()) return
      let after = null
      try {
        after = await collectWorkspaceFiles(path => listDir(path, sessionId))
      } catch {
        return
      }
      if (!stillCurrent() || !workspaceHasNewFiles(baseline, after)) return
      onExpand()
    },
  }
}
