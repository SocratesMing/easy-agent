#!/usr/bin/env node
/**
 * 把 frontend-shared/ 下的共享前端代码同步到两套前端（frontend-vue2 / frontend）。
 *
 * 为什么是"复制"而不是软链/包引用：两套前端的构建器（webpack 5 / Vite）根目录不同，
 * 共享代码里的 `marked` 等裸依赖必须能按各自的 node_modules 解析；放进各自 src 下
 * 最省事也最稳。复制带来的漂移由 `--check`（frontend-vue2 的 npm run check:easy-agent 会调用）
 * 与两侧 structure.test.js 兜住。
 *
 * 用法：
 *   node scripts/dev/sync-frontend-shared.mjs           # 写入
 *   node scripts/dev/sync-frontend-shared.mjs --check   # 只校验，不一致退出码 1
 */

import { readdir, readFile, writeFile, mkdir, unlink } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const sourceRoot = path.join(repoRoot, 'frontend-shared')
const targets = [
  // Vue2 基线的共享代码属于受管业务模块（随模块整体同步到内网宿主，宿主不再需要 frontend-shared/）
  path.join(repoRoot, 'frontend-vue2/src/packages/easyAgent/shared'),
  path.join(repoRoot, 'frontend/src/shared'),
]

const BANNER = [
  '// ⚠️ 自动生成，请勿直接编辑。',
  '// 源文件：frontend-shared/{rel}',
  '// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）',
  '',
].join('\n')

async function collect(dir, prefix = '') {
  const entries = await readdir(dir, { withFileTypes: true })
  const files = []
  for (const entry of entries) {
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name
    if (entry.isDirectory()) {
      files.push(...(await collect(path.join(dir, entry.name), rel)))
    } else if (entry.isFile() && entry.name.endsWith('.js')) {
      files.push(rel)
    }
  }
  return files
}

/** 目标目录里带生成头、但源文件已删除的副本（源文件删掉后副本不会自己消失） */
async function findOrphans(targetRoot, sourceFiles) {
  let copies = []
  try {
    copies = await collect(targetRoot)
  } catch {
    return []
  }
  const orphans = []
  for (const rel of copies) {
    if (sourceFiles.includes(rel)) continue
    const head = (await readFile(path.join(targetRoot, rel), 'utf8')).split('\n', 2).join('\n')
    if (head === BANNER.replace('{rel}', rel).split('\n', 2).join('\n')) orphans.push(path.join(targetRoot, rel))
  }
  return orphans
}

async function main() {
  const checkOnly = process.argv.includes('--check')
  const files = await collect(sourceRoot)
  const problems = []

  for (const targetRoot of targets) {
    for (const orphan of await findOrphans(targetRoot, files)) {
      if (checkOnly) {
        problems.push(`${path.relative(repoRoot, orphan)}（源文件已删除，副本残留）`)
        continue
      }
      await unlink(orphan)
      console.log(`removed orphan: ${path.relative(repoRoot, orphan)}`)
    }
  }

  for (const rel of files) {
    const source = await readFile(path.join(sourceRoot, rel), 'utf8')
    const expected = BANNER.replace('{rel}', rel) + source

    for (const targetRoot of targets) {
      const targetPath = path.join(targetRoot, rel)
      let current = null
      try {
        current = await readFile(targetPath, 'utf8')
      } catch {
        current = null
      }

      if (current === expected) continue

      if (checkOnly) {
        problems.push(path.relative(repoRoot, targetPath))
        continue
      }

      await mkdir(path.dirname(targetPath), { recursive: true })
      await writeFile(targetPath, expected)
      console.log(`synced: ${path.relative(repoRoot, targetPath)}`)
    }
  }

  if (problems.length > 0) {
    console.error('共享代码未同步（请执行 npm run sync:shared）：')
    for (const problem of problems) console.error(`  - ${problem}`)
    process.exit(1)
  }
}

await main()
