#!/usr/bin/env node
/**
 * 单元测试入口（npm run test:unit）：收集 src/**\/*.test.js 与 tests/*.test.js，交给 node --test。
 *
 * - 测试工具只在外网使用，需要 Node ≥ 20.11（node:test、import.meta.dirname）；
 *   构建目标仍兼容 Node 14（npm run build / build:sdk），两者的支持边界分开声明。
 * - 一个测试文件都没找到时直接失败：目录迁移后 glob 落空不能被当成「全部通过」。
 */
import { spawnSync } from 'child_process'
import { existsSync, readdirSync } from 'fs'
import { dirname, join, relative, resolve } from 'path'
import { fileURLToPath } from 'url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const [major, minor] = process.versions.node.split('.').map(Number)
if (major < 20 || (major === 20 && minor < 11)) {
  console.error(`[test:unit] 需要 Node ≥ 20.11（当前 ${process.version}）；Node 14 只支持构建，不支持运行单元测试`)
  process.exit(1)
}

function collect(dir, out = []) {
  if (!existsSync(dir)) return out
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) collect(full, out)
    else if (entry.name.endsWith('.test.js')) out.push(relative(root, full))
  }
  return out
}

const files = [...collect(join(root, 'src')), ...collect(join(root, 'tests'))].sort()
if (files.length === 0) {
  console.error('[test:unit] 没有找到任何 *.test.js，拒绝把空集当作通过')
  process.exit(1)
}
console.log(`[test:unit] ${files.length} 个测试文件`)
const result = spawnSync(process.execPath, ['--test', ...files, ...process.argv.slice(2)], { cwd: root, stdio: 'inherit' })
process.exit(result.status === null ? 1 : result.status)
