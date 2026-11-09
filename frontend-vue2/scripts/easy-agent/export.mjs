#!/usr/bin/env node
/**
 * 导出 Easy Agent 迁移包（npm run export:easy-agent -- [选项]）。
 *
 * 产物（默认 frontend-vue2/dist-easy-agent/easy-agent-<时间>-<提交>/）：
 *   manifest.json            受管路径、逐文件 sha256、来源提交 / 工作区状态、依赖清单、合同版本、相对基线的变化
 *   module/src/packages/easyAgent/**   业务模块完整源码（不含 *.test.js），路径与宿主一致
 *   integration/**           首次接入模板（host-template）、同步工具（tools）、接入文档 —— 不会自动覆盖宿主
 *   CHANGES.md               人读的变化说明：新增 / 修改 / 删除、依赖变化、合同变化
 *
 * 选项：
 *   --baseline <manifest.json>  相对上一版迁移包（或内网同步状态文件 .easy-agent-sync.json）生成变化清单
 *   --baseline-git <ref>        相对某个 Git 提交里的模块生成变化清单
 *   --out <dir>                 输出目录（默认 dist-easy-agent/）
 *   --archive                   另外打一个 .tar.gz（需要系统 tar）
 *   --check                     先运行 check:easy-agent（默认开启，--no-check 关闭）
 * 没有基线时输出「首次导入」清单，不虚构历史差异。
 */
import { execFileSync, spawnSync } from 'child_process'
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { basename, dirname, join, relative, resolve } from 'path'
import { DEPENDENCIES_FILE } from './deps.mjs'
import {
  FRONTEND_ROOT,
  INTEGRATION_ROOT,
  MODULE_REL,
  MODULE_ROOT,
  REPO_ROOT,
  fileDigest,
  formatJson,
  isManagedFile,
  listFiles,
  listModuleFiles,
  readJson,
  sha256,
  toPosix,
} from './lib.mjs'

export const MANIFEST_SCHEMA = 1

function arg(name) {
  const index = process.argv.indexOf(name)
  return index === -1 ? null : process.argv[index + 1]
}

function git(args) {
  try {
    return execFileSync('git', args, { cwd: REPO_ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim()
  } catch (_) {
    return null
  }
}

function gitInfo() {
  const commit = git(['rev-parse', 'HEAD'])
  if (!commit) return null
  const moduleGitPath = toPosix(relative(REPO_ROOT, MODULE_ROOT))
  const status = git(['status', '--porcelain', '--', moduleGitPath]) || ''
  const dirtyFiles = status.split('\n').filter(Boolean).map((line) => line.slice(3))
  return {
    commit,
    branch: git(['rev-parse', '--abbrev-ref', 'HEAD']),
    // 模块目录的未提交改动也进了迁移包：记录下来，便于内网追溯
    dirty: dirtyFiles.length > 0,
    dirtyFiles,
  }
}

function contractVersion() {
  const code = readFileSync(join(MODULE_ROOT, 'runtime', 'config.js'), 'utf8')
  const match = /export const EASY_AGENT_CONTRACT_VERSION = (\d+)/.exec(code)
  if (!match) throw new Error('runtime/config.js 中找不到 EASY_AGENT_CONTRACT_VERSION')
  return Number(match[1])
}

/** 当前模块的文件清单（路径相对宿主项目根，如 src/packages/easyAgent/index.js） */
export function currentFiles() {
  return listModuleFiles().map((rel) => {
    const digest = fileDigest(join(MODULE_ROOT, rel))
    return { path: `${MODULE_REL}/${rel}`, sha256: digest.sha256, size: digest.size }
  })
}

function baselineFromManifest(file) {
  const data = readJson(resolve(file))
  // 迁移包 manifest 与内网同步状态文件（.easy-agent-sync.json）结构相同：都有 files[] 与 dependencies
  if (!Array.isArray(data.files)) throw new Error(`${file} 不是有效的 manifest（缺少 files）`)
  return {
    info: { type: 'manifest', file: basename(file), generatedAt: data.generatedAt || null, commit: (data.source && data.source.git && data.source.git.commit) || null },
    files: data.files,
    dependencies: data.dependencies || null,
    contractVersion: data.contractVersion || null,
  }
}

function baselineFromGit(ref) {
  const modulePath = toPosix(relative(REPO_ROOT, MODULE_ROOT))
  const list = git(['ls-tree', '-r', '--name-only', ref, '--', modulePath])
  if (list === null) throw new Error(`无法读取 Git 基线 ${ref}`)
  const files = []
  let dependencies = null
  let contract = null
  for (const repoPath of list.split('\n').filter(Boolean)) {
    const rel = repoPath.slice(modulePath.length + 1)
    if (!isManagedFile(rel)) continue
    const content = execFileSync('git', ['show', `${ref}:${repoPath}`], { cwd: REPO_ROOT, maxBuffer: 64 * 1024 * 1024 })
    files.push({ path: `${MODULE_REL}/${rel}`, sha256: sha256(content), size: content.length })
    if (rel === 'dependencies.json') dependencies = JSON.parse(content.toString('utf8')).runtime || null
    if (rel === 'runtime/config.js') {
      const match = /export const EASY_AGENT_CONTRACT_VERSION = (\d+)/.exec(content.toString('utf8'))
      contract = match ? Number(match[1]) : null
    }
  }
  if (files.length === 0) throw new Error(`Git 基线 ${ref} 中没有 ${modulePath}（该提交早于模块化改造时请改用 --baseline 或首次导入）`)
  return { info: { type: 'git', ref, commit: git(['rev-parse', ref]) }, files, dependencies, contractVersion: contract }
}

export function diffFiles(previous, next) {
  const before = new Map(previous.map((f) => [f.path, f.sha256]))
  const after = new Map(next.map((f) => [f.path, f.sha256]))
  const added = []
  const modified = []
  const deleted = []
  for (const [path, hash] of after) {
    if (!before.has(path)) added.push(path)
    else if (before.get(path) !== hash) modified.push(path)
  }
  for (const path of before.keys()) if (!after.has(path)) deleted.push(path)
  return { added: added.sort(), modified: modified.sort(), deleted: deleted.sort() }
}

export function diffDependencies(previous, next) {
  if (!previous) return null
  const added = {}
  const removed = {}
  const changed = {}
  for (const name of Object.keys(next)) {
    if (!previous[name]) added[name] = next[name].range
    else if (previous[name].range !== next[name].range || previous[name].locked !== next[name].locked) {
      changed[name] = { from: `${previous[name].range} (${previous[name].locked})`, to: `${next[name].range} (${next[name].locked})` }
    }
  }
  for (const name of Object.keys(previous)) if (!next[name]) removed[name] = previous[name].range
  return { added, removed, changed }
}

function renderChanges(manifest) {
  const lines = ['# Easy Agent 迁移包变化说明', '']
  const src = manifest.source.git
  lines.push(`- 生成时间：${manifest.generatedAt}`)
  lines.push(src ? `- 来源提交：${src.commit}（${src.branch}）${src.dirty ? `，**含未提交改动**：${src.dirtyFiles.join('、')}` : ''}` : '- 来源：无 Git 信息（来源未知）')
  lines.push(`- 适配合同版本：${manifest.contractVersion}`)
  lines.push(`- 受管文件：${manifest.files.length} 个（${manifest.managedRoot}/）`, '')
  if (!manifest.baseline) {
    lines.push('## 首次导入', '', '没有指定基线：全部文件按「新增」处理，不生成历史差异。首次接入请按 integration/README.md「首次接入」操作。', '')
  } else {
    const b = manifest.baseline
    lines.push(`## 相对基线的变化（${b.type === 'git' ? `Git ${b.ref}` : b.file}${b.commit ? `，${b.commit.slice(0, 12)}` : ''}）`, '')
    for (const [title, list] of [['新增', manifest.changes.added], ['修改', manifest.changes.modified], ['删除', manifest.changes.deleted]]) {
      lines.push(`### ${title}（${list.length}）`, '', ...(list.length ? list.map((p) => `- ${p}`) : ['- 无']), '')
    }
    lines.push('删除只在宿主文件与上一版 manifest 记录一致时执行；宿主改过的文件会报冲突、默认保留。', '')
  }
  const dep = manifest.dependencyChanges
  lines.push('## 依赖变化', '')
  if (!dep) lines.push('- 无基线依赖清单：请按 dependencies.json 与 integration/README.md「依赖」核对宿主。')
  else if (!Object.keys(dep.added).length && !Object.keys(dep.removed).length && !Object.keys(dep.changed).length) lines.push('- 无')
  else {
    for (const [name, range] of Object.entries(dep.added)) lines.push(`- ⚠️ 新增 ${name}@${range}：宿主需安装后再构建`)
    for (const [name, range] of Object.entries(dep.removed)) lines.push(`- 移除 ${name}@${range}：宿主若无其他用途可卸载（不卸载不影响构建）`)
    for (const [name, change] of Object.entries(dep.changed)) lines.push(`- ⚠️ 变更 ${name}：${change.from} → ${change.to}`)
  }
  lines.push('')
  lines.push('## 适配合同', '')
  lines.push(manifest.contractChanged
    ? `- ⚠️ 合同版本 ${manifest.contractChanged.from} → ${manifest.contractChanged.to}：按 integration/README.md「合同变更记录」调整宿主适配器后再上线`
    : '- 未变化（宿主适配器无需调整）')
  lines.push('')
  return `${lines.join('\n')}\n`
}

function copyTree(fromRoot, files, toRoot) {
  for (const rel of files) {
    const target = join(toRoot, rel)
    mkdirSync(dirname(target), { recursive: true })
    copyFileSync(join(fromRoot, rel), target)
  }
}

function main() {
  if (!process.argv.includes('--no-check')) {
    const check = spawnSync(process.execPath, [join(FRONTEND_ROOT, 'scripts', 'easy-agent', 'check.mjs')], { stdio: 'inherit' })
    if (check.status !== 0) {
      console.error('[export] check:easy-agent 未通过，停止导出（如确需导出请先修复）')
      process.exit(1)
    }
  }
  const files = currentFiles()
  const dependencies = readJson(DEPENDENCIES_FILE).runtime
  const source = { git: gitInfo() }
  if (!source.git) source.note = '无 Git 信息：迁移包来源未知'
  let baseline = null
  if (arg('--baseline')) baseline = baselineFromManifest(arg('--baseline'))
  else if (arg('--baseline-git')) baseline = baselineFromGit(arg('--baseline-git'))

  const version = contractVersion()
  const manifest = {
    schemaVersion: MANIFEST_SCHEMA,
    package: 'easy-agent',
    managedRoot: MODULE_REL,
    contractVersion: version,
    generatedAt: new Date().toISOString(),
    source,
    baseline: baseline ? baseline.info : null,
    changes: baseline ? diffFiles(baseline.files, files) : null,
    dependencyChanges: baseline ? diffDependencies(baseline.dependencies, dependencies) : null,
    contractChanged: baseline && baseline.contractVersion && baseline.contractVersion !== version
      ? { from: baseline.contractVersion, to: version }
      : null,
    dependencies,
    files,
  }

  const stamp = manifest.generatedAt.replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const name = `easy-agent-${stamp}-${source.git ? source.git.commit.slice(0, 8) : 'nogit'}`
  const outRoot = resolve(arg('--out') || join(FRONTEND_ROOT, 'dist-easy-agent'))
  const outDir = join(outRoot, name)
  if (existsSync(outDir)) throw new Error(`输出目录已存在：${outDir}`)
  copyTree(MODULE_ROOT, listModuleFiles(), join(outDir, 'module', MODULE_REL))
  const integrationFiles = listFiles(INTEGRATION_ROOT).filter((rel) => !/(^|\/)(node_modules|dist)\//.test(rel))
  copyTree(INTEGRATION_ROOT, integrationFiles, join(outDir, 'integration'))
  writeFileSync(join(outDir, 'manifest.json'), formatJson(manifest))
  writeFileSync(join(outDir, 'CHANGES.md'), renderChanges(manifest))

  // 自检：包内文件与 manifest 逐一比对，失败即视为导出失败
  for (const entry of files) {
    const copied = join(outDir, 'module', entry.path)
    if (!existsSync(copied) || fileDigest(copied).sha256 !== entry.sha256) throw new Error(`导出校验失败：${entry.path}`)
  }

  let archive = null
  if (process.argv.includes('--archive')) {
    archive = `${outDir}.tar.gz`
    const tar = spawnSync('tar', ['-czf', archive, '-C', outRoot, name], { stdio: 'inherit' })
    if (tar.status !== 0) throw new Error('打包 .tar.gz 失败（系统缺少 tar？可直接拷贝目录）')
  }

  console.log(`\n[export] 迁移包：${outDir}`)
  if (archive) console.log(`[export] 压缩包：${archive}`)
  console.log(`  受管文件 ${files.length} 个，合同版本 ${version}，来源 ${source.git ? `${source.git.commit.slice(0, 12)}${source.git.dirty ? '（含未提交改动）' : ''}` : '未知'}`)
  if (manifest.changes) {
    const c = manifest.changes
    console.log(`  相对基线：新增 ${c.added.length}，修改 ${c.modified.length}，删除 ${c.deleted.length}`)
  } else {
    console.log('  未指定基线：首次导入清单')
  }
  if (manifest.contractChanged) console.log(`  ⚠️ 适配合同版本变化：${manifest.contractChanged.from} → ${manifest.contractChanged.to}`)
  console.log('  内网同步：node integration/tools/sync-easy-agent.cjs --package <本目录> --target <宿主项目根>（先预览，再加 --apply）\n')
}

if (process.argv[1] && process.argv[1].endsWith('export.mjs')) {
  try {
    main()
  } catch (error) {
    console.error(`[export] ${error.message}`)
    process.exit(1)
  }
}
