#!/usr/bin/env node
/**
 * Easy Agent 内网同步工具（Node ≥ 14，无第三方依赖，CommonJS：不受宿主 package.json "type" 影响）。
 *
 * 只操作迁移包 manifest 声明的受管目录（src/packages/easyAgent/），不碰宿主适配器、页面包装、
 * 路由、公共目录、package.json、锁文件与构建配置；不安装依赖、不改路由。
 *
 * 用法：
 *   预览（默认，不写任何文件）：
 *     node sync-easy-agent.cjs --package <迁移包目录> --target <宿主项目根>
 *   应用（先备份，失败自动恢复；有冲突时拒绝，除非 --force）：
 *     node sync-easy-agent.cjs --package <迁移包目录> --target <宿主项目根> --apply [--force] [--backup-dir <目录>]
 *   回滚到某次应用之前：
 *     node sync-easy-agent.cjs --target <宿主项目根> --rollback <备份目录>
 *   首次迁移：扫描旧目录（api/easyAgent、utils/easyAgent、pages/easyAgent 等）并查引用，只出报告不删除：
 *     node sync-easy-agent.cjs --package <迁移包目录> --target <宿主项目根> --legacy-scan [--legacy-dir <相对路径>]...
 *
 * 退出码：0 成功 / 预览无冲突；1 失败（清单无效、校验失败、应用出错已恢复）；2 存在冲突（未写入）。
 */
'use strict'

const { spawnSync } = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const STATE_FILE = '.easy-agent-sync.json'
const BACKUP_INDEX = 'backup.json'
const DEFAULT_LEGACY_DIRS = ['src/api/easyAgent', 'src/utils/easyAgent', 'src/pages/easyAgent', 'src/mixins', 'src/components/easyAgent']

// ── 参数 ───────────────────────────────────────────────────────────
function parseArgs(argv) {
  const opts = { legacyDirs: [] }
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i]
    const next = () => {
      i += 1
      if (i >= argv.length) throw new UsageError(`${a} 需要参数`)
      return argv[i]
    }
    if (a === '--package') opts.package = next()
    else if (a === '--target') opts.target = next()
    else if (a === '--apply') opts.apply = true
    else if (a === '--force') opts.force = true
    else if (a === '--backup-dir') opts.backupDir = next()
    else if (a === '--rollback') opts.rollback = next()
    else if (a === '--legacy-scan') opts.legacyScan = true
    else if (a === '--legacy-dir') opts.legacyDirs.push(next())
    else if (a === '--json') opts.json = true
    else if (a === '-h' || a === '--help') opts.help = true
    else throw new UsageError(`未知参数 ${a}`)
  }
  return opts
}

class UsageError extends Error {}
class ConflictError extends Error {}

// ── 工具 ───────────────────────────────────────────────────────────
function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'))
}

function writeFileAtomic(file, content) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  const tmp = `${file}.easy-agent-tmp`
  fs.writeFileSync(tmp, content)
  fs.renameSync(tmp, file)
}

function copyFileAtomic(from, to) {
  fs.mkdirSync(path.dirname(to), { recursive: true })
  const tmp = `${to}.easy-agent-tmp`
  fs.copyFileSync(from, tmp)
  fs.renameSync(tmp, to)
}

function isFile(file) {
  try {
    return fs.lstatSync(file).isFile()
  } catch (_) {
    return false
  }
}

function listFiles(dir) {
  const out = []
  const walk = (current) => {
    let entries
    try {
      entries = fs.readdirSync(current, { withFileTypes: true })
    } catch (_) {
      return
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name)
      if (entry.isDirectory()) walk(full)
      else out.push(path.relative(dir, full).split(path.sep).join('/'))
    }
  }
  walk(dir)
  return out.sort()
}

function stamp() {
  return new Date().toISOString().replace(/[-:.]/g, '')
}

/** 默认备份目录：<宿主>/.easy-agent-backups/<时间戳>，同一毫秒内重复应用时加序号 */
function defaultBackupDir(target) {
  const base = path.join(target, '.easy-agent-backups', stamp())
  let dir = base
  for (let i = 2; fs.existsSync(dir); i += 1) dir = `${base}-${i}`
  return dir
}

/** manifest 路径校验：相对、规范、位于受管根目录下，防止 ../ 越界与绝对路径写入 */
function assertSafePath(p, managedRoot, allowState) {
  if (typeof p !== 'string' || !p) throw new Error('manifest 里有空路径')
  if (p.indexOf('\0') !== -1 || p.indexOf('\\') !== -1) throw new Error(`非法路径：${p}`)
  if (path.posix.isAbsolute(p) || /^[a-zA-Z]:/.test(p)) throw new Error(`不允许绝对路径：${p}`)
  const normalized = path.posix.normalize(p)
  if (normalized !== p || p.split('/').indexOf('..') !== -1) throw new Error(`路径不规范或越界：${p}`)
  if (p.indexOf(`${managedRoot}/`) !== 0) throw new Error(`路径不在受管目录 ${managedRoot}/ 下：${p}`)
  if (!allowState && path.posix.basename(p) === STATE_FILE) throw new Error(`manifest 不能包含同步状态文件：${p}`)
}

function loadManifest(file, label) {
  const data = readJson(file)
  if (data.schemaVersion !== 1) throw new Error(`${label}: 不支持的 manifest 版本 ${data.schemaVersion}`)
  const root = data.managedRoot
  if (typeof root !== 'string' || !/^src\/[A-Za-z0-9_\-/]+$/.test(root) || root.split('/').indexOf('..') !== -1) {
    throw new Error(`${label}: managedRoot 无效：${root}`)
  }
  if (!Array.isArray(data.files)) throw new Error(`${label}: 缺少 files 清单`)
  const seen = new Set()
  for (const entry of data.files) {
    assertSafePath(entry.path, root)
    if (!/^[0-9a-f]{64}$/.test(entry.sha256 || '')) throw new Error(`${label}: ${entry.path} 缺少有效 sha256`)
    if (seen.has(entry.path)) throw new Error(`${label}: 重复路径 ${entry.path}`)
    seen.add(entry.path)
  }
  return data
}

/** 目标路径解析，并确认真实路径（跟随符号链接后）仍在宿主项目内 */
function hostPath(target, rel) {
  const full = path.resolve(target, rel)
  if (full.indexOf(path.resolve(target) + path.sep) !== 0) throw new Error(`越界路径：${rel}`)
  return full
}

function assertInsideTarget(target, managedRoot) {
  const realTarget = fs.realpathSync(target)
  let probe = path.resolve(target, managedRoot)
  // 找到已存在的最近一级目录再比真实路径（受管目录可能尚未创建）
  while (!fs.existsSync(probe) && probe !== path.dirname(probe)) probe = path.dirname(probe)
  const real = fs.realpathSync(probe)
  if (real !== realTarget && real.indexOf(realTarget + path.sep) !== 0) {
    throw new Error(`受管目录经符号链接指向宿主项目之外：${real}`)
  }
}

// ── 计划 ───────────────────────────────────────────────────────────
function verifyPackage(pkgDir, manifest) {
  const moduleDir = path.join(pkgDir, 'module')
  for (const entry of manifest.files) {
    const file = path.join(moduleDir, entry.path)
    if (!isFile(file)) throw new Error(`迁移包缺少文件：${entry.path}`)
    if (sha256File(file) !== entry.sha256) throw new Error(`迁移包文件校验失败（被修改或损坏）：${entry.path}`)
  }
  const declared = new Set(manifest.files.map((e) => e.path))
  const extra = listFiles(path.join(moduleDir, manifest.managedRoot))
    .map((rel) => `${manifest.managedRoot}/${rel}`)
    .filter((p) => !declared.has(p))
  if (extra.length) throw new Error(`迁移包里有 manifest 未声明的文件（清单不完整）：${extra.join(', ')}`)
}

function buildPlan(target, manifest, state) {
  const prev = new Map(((state && state.files) || []).map((e) => [e.path, e]))
  const next = new Map(manifest.files.map((e) => [e.path, e]))
  const plan = { add: [], update: [], unchanged: [], delete: [], conflicts: [], unmanaged: [], restored: [] }
  for (const entry of manifest.files) {
    const file = hostPath(target, entry.path)
    if (!isFile(file)) {
      if (fs.existsSync(file)) plan.conflicts.push({ path: entry.path, reason: '目标位置是目录或特殊文件' })
      else if (prev.has(entry.path)) plan.restored.push(entry.path)
      else plan.add.push(entry.path)
      continue
    }
    const current = sha256File(file)
    if (current === entry.sha256) plan.unchanged.push(entry.path)
    else if (prev.has(entry.path) && prev.get(entry.path).sha256 === current) plan.update.push(entry.path)
    else plan.conflicts.push({ path: entry.path, reason: prev.has(entry.path) ? '宿主本地修改过该受管文件' : '同路径已有未纳管的文件' })
  }
  for (const entry of prev.values()) {
    if (next.has(entry.path)) continue
    assertSafePath(entry.path, manifest.managedRoot)
    const file = hostPath(target, entry.path)
    if (!isFile(file)) continue
    if (sha256File(file) === entry.sha256) plan.delete.push(entry.path)
    else plan.conflicts.push({ path: entry.path, reason: '上一版受管文件已被宿主修改，本版已删除（不自动删除）', deleted: true })
  }
  const managedDir = hostPath(target, manifest.managedRoot)
  for (const rel of listFiles(managedDir)) {
    const p = `${manifest.managedRoot}/${rel}`
    if (rel === STATE_FILE || next.has(p) || prev.has(p) || /\.easy-agent-tmp$/.test(rel)) continue
    plan.unmanaged.push(p)
  }
  return plan
}

/**
 * 宿主是 Git 仓库时，列出被宿主 .gitignore 忽略的受管文件（如通用的 `workspace/` 规则会顺带忽略
 * features/workspace/）：这些文件在本机存在、构建也能过，但提交时会被漏掉。没有 git 或不是仓库时返回 []。
 */
function gitIgnoredPaths(target, paths) {
  if (!paths.length) return []
  const r = spawnSync('git', ['-C', target, 'check-ignore', '--stdin'], { input: paths.join('\n'), encoding: 'utf8' })
  if (r.error || r.status !== 0) return []
  return r.stdout.split('\n').map((l) => l.trim()).filter(Boolean)
}

function warnGitIgnored(ignored) {
  if (!ignored.length) return
  console.log(`\n⚠️⚠️ 有 ${ignored.length} 个受管文件被宿主 .gitignore 忽略，提交时会漏掉（构建机 / 其他同事拿不到）：`)
  for (const p of ignored.slice(0, 20)) console.log(`  - ${p}`)
  if (ignored.length > 20) console.log(`  …… 其余 ${ignored.length - 20} 个`)
  console.log('  请在宿主 .gitignore 末尾加反向规则（例如 !src/packages/easyAgent/** 及被忽略的目录），或对这些文件 git add -f')
}

function diffDeps(before, after) {
  const changes = []
  const a = before || {}
  const b = after || {}
  for (const name of Object.keys(b)) {
    if (!a[name]) changes.push(`新增 ${name}@${b[name].range}`)
    else if (a[name].range !== b[name].range || a[name].locked !== b[name].locked) {
      changes.push(`变更 ${name}：${a[name].range}（${a[name].locked}）→ ${b[name].range}（${b[name].locked}）`)
    }
  }
  for (const name of Object.keys(a)) if (!b[name]) changes.push(`移除 ${name}@${a[name].range}`)
  return changes
}

function printPlan(plan, manifest, state, opts) {
  const log = (s) => console.log(s)
  const src = manifest.source && manifest.source.git
  log(`迁移包：${manifest.generatedAt}  来源 ${src ? `${src.commit.slice(0, 12)}${src.dirty ? '（含未提交改动）' : ''}` : '未知（无 Git 信息）'}  合同版本 ${manifest.contractVersion}`)
  log(state ? `宿主当前：${state.generatedAt}（${state.appliedAt} 应用）` : '宿主当前：未找到同步状态（首次导入）')
  const section = (title, list, fmt) => {
    log(`\n${title}（${list.length}）`)
    for (const item of list.slice(0, 200)) log(`  ${fmt ? fmt(item) : item}`)
    if (list.length > 200) log(`  …… 其余 ${list.length - 200} 项`)
  }
  section('新增', plan.add)
  section('更新', plan.update)
  section('删除（上一版受管、本版移除、宿主未改动）', plan.delete)
  if (plan.restored.length) section('补回（受管文件在宿主被删掉了）', plan.restored)
  section('冲突（默认不覆盖、不删除）', plan.conflicts, (c) => `${c.path} —— ${c.reason}`)
  if (plan.unmanaged.length) section('受管目录内的未纳管文件（保持不动，请人工确认是否需要）', plan.unmanaged)
  log(`\n未变化：${plan.unchanged.length} 个`)
  const depChanges = state ? diffDeps(state.dependencies, manifest.dependencies) : null
  if (depChanges === null) {
    log('\n依赖：首次导入，请按迁移包 integration/README.md「依赖」核对宿主 package.json（本工具不装包）')
  } else if (depChanges.length) {
    log('\n⚠️⚠️ 依赖有变化（本工具不会安装，请在宿主 package.json 中处理后再构建）：')
    for (const c of depChanges) log(`  - ${c}`)
  }
  if (state && state.contractVersion !== manifest.contractVersion) {
    log(`\n⚠️⚠️ 适配合同版本 ${state.contractVersion} → ${manifest.contractVersion}：按 integration/README.md「合同变更记录」调整 src/integrations/easyAgent.js 后再上线`)
  }
  if (!opts.apply) log('\n这是预览，没有写入任何文件。确认后加 --apply 执行。')
}

// ── 应用 / 回滚 ─────────────────────────────────────────────────────
function applyPlan(target, pkgDir, manifest, state, plan, opts) {
  const conflicts = plan.conflicts
  if (conflicts.length && !opts.force) {
    throw new ConflictError(`存在 ${conflicts.length} 个冲突，未写入任何文件。请先处理（把宿主改动回流外网或还原），或确认后加 --force 覆盖（会先备份）`)
  }
  const writes = plan.add.concat(plan.update, plan.restored, conflicts.filter((c) => !c.deleted).map((c) => c.path))
  const deletes = plan.delete.concat(conflicts.filter((c) => c.deleted).map((c) => c.path))
  const stateFile = hostPath(target, `${manifest.managedRoot}/${STATE_FILE}`)
  const nextState = `${JSON.stringify(Object.assign({}, manifest, { appliedAt: new Date().toISOString() }), null, 2)}\n`
  if (!writes.length && !deletes.length && state && state.generatedAt === manifest.generatedAt) {
    console.log('\n无变化：宿主已是该迁移包的内容（未写入）')
    return null
  }

  const backupDir = path.resolve(opts.backupDir || defaultBackupDir(target))
  if (fs.existsSync(backupDir) && listFiles(backupDir).length) throw new Error(`备份目录已存在且非空：${backupDir}`)
  const index = { createdAt: new Date().toISOString(), target: path.resolve(target), managedRoot: manifest.managedRoot, actions: [] }
  const backup = (rel, action) => {
    const file = hostPath(target, rel)
    if (isFile(file)) {
      const copy = path.join(backupDir, 'files', rel)
      fs.mkdirSync(path.dirname(copy), { recursive: true })
      fs.copyFileSync(file, copy)
      index.actions.push({ path: rel, action, backup: true })
    } else {
      index.actions.push({ path: rel, action: 'created', backup: false })
    }
  }
  fs.mkdirSync(backupDir, { recursive: true })
  for (const rel of writes) backup(rel, 'overwritten')
  for (const rel of deletes) backup(rel, 'deleted')
  backup(`${manifest.managedRoot}/${STATE_FILE}`, 'overwritten')
  writeFileAtomic(path.join(backupDir, BACKUP_INDEX), `${JSON.stringify(index, null, 2)}\n`)

  try {
    for (const rel of writes) copyFileAtomic(path.join(pkgDir, 'module', rel), hostPath(target, rel))
    for (const rel of deletes) {
      fs.unlinkSync(hostPath(target, rel))
      removeEmptyDirs(target, manifest.managedRoot, path.posix.dirname(rel))
    }
    if (process.env.EASY_AGENT_SYNC_FAIL_AFTER_WRITE === '1') throw new Error('模拟写入后失败（测试用）')
    for (const entry of manifest.files) {
      const file = hostPath(target, entry.path)
      if (!isFile(file) || sha256File(file) !== entry.sha256) throw new Error(`写入后校验失败：${entry.path}`)
    }
    writeFileAtomic(stateFile, nextState)
  } catch (error) {
    console.error(`\n应用失败：${error.message}\n正在按备份恢复……`)
    restoreBackup(target, backupDir)
    error.restored = true
    throw error
  }
  return backupDir
}

function removeEmptyDirs(target, managedRoot, relDir) {
  let dir = relDir
  while (dir && dir !== managedRoot && dir.indexOf(`${managedRoot}/`) === 0) {
    const full = hostPath(target, dir)
    try {
      if (fs.readdirSync(full).length) break
      fs.rmdirSync(full)
    } catch (_) {
      break
    }
    dir = path.posix.dirname(dir)
  }
}

function restoreBackup(target, backupDir) {
  const index = readJson(path.join(backupDir, BACKUP_INDEX))
  if (path.resolve(index.target) !== path.resolve(target)) {
    throw new Error(`备份属于 ${index.target}，与 --target 不一致`)
  }
  for (const action of index.actions) {
    assertSafePath(action.path, index.managedRoot, true)
    const file = hostPath(target, action.path)
    if (action.backup) copyFileAtomic(path.join(backupDir, 'files', action.path), file)
    else if (isFile(file)) {
      fs.unlinkSync(file)
      removeEmptyDirs(target, index.managedRoot, path.posix.dirname(action.path))
    }
  }
}

// ── 首次迁移：旧目录扫描 ─────────────────────────────────────────────
function legacyScan(target, manifest, dirs) {
  const moduleFiles = manifest.files.map((e) => e.path)
  const byHash = new Map(manifest.files.map((e) => [e.sha256, e.path]))
  const byName = new Map()
  for (const p of moduleFiles) {
    const name = path.posix.basename(p)
    if (!byName.has(name)) byName.set(name, [])
    byName.get(name).push(p)
  }
  const legacy = []
  for (const dir of dirs) {
    const full = hostPath(target, dir)
    for (const rel of listFiles(full)) {
      const p = `${dir}/${rel}`
      const hash = sha256File(path.join(full, rel))
      const same = byHash.get(hash)
      const candidates = byName.get(path.posix.basename(rel)) || []
      legacy.push({
        path: p,
        match: same ? `内容与 ${same} 完全相同` : candidates.length ? `同名：${candidates.join('、')}（内容不同，可能有内网定制）` : '模块中无同名文件（可能是内网自有文件）',
        identical: !!same,
      })
    }
  }
  // 查引用：宿主源码里仍然 import 旧目录的位置（受管目录与旧目录自身除外）
  const refs = []
  const srcDir = hostPath(target, 'src')
  const patterns = dirs.map((d) => d.replace(/^src\//, ''))
  for (const rel of listFiles(srcDir)) {
    if (!/\.(js|vue|ts|jsx|tsx|scss|less|css)$/.test(rel)) continue
    const p = `src/${rel}`
    if (p.indexOf(`${manifest.managedRoot}/`) === 0 || dirs.some((d) => p.indexOf(`${d}/`) === 0)) continue
    const lines = fs.readFileSync(path.join(srcDir, rel), 'utf8').split('\n')
    lines.forEach((line, i) => {
      for (const pattern of patterns) {
        if (line.indexOf(pattern) !== -1 && /\b(import|require|from)\b|import\(/.test(line)) refs.push({ file: p, line: i + 1, text: line.trim(), legacy: pattern })
      }
    })
  }
  console.log('旧目录文件（只报告，不删除；内网定制需先人工比对后再处理）：')
  for (const item of legacy) console.log(`  ${item.path} —— ${item.match}`)
  if (!legacy.length) console.log('  （未发现）')
  console.log('\n宿主源码中仍引用旧目录的位置（迁移到新入口 @/packages/easyAgent 或删除后才能清理旧目录）：')
  for (const r of refs) console.log(`  ${r.file}:${r.line}  ${r.text}`)
  if (!refs.length) console.log('  （未发现）')
  console.log('\n提示：上面「内容完全相同」且无引用的旧文件才是低风险清理候选；本工具不生成删除命令。')
  return { legacy, refs }
}

// ── 入口 ───────────────────────────────────────────────────────────
function main(argv) {
  const opts = parseArgs(argv)
  if (opts.help || (!opts.package && !opts.rollback)) {
    console.log(fs.readFileSync(__filename, 'utf8').split('\n').slice(1, 21).join('\n').replace(/^ \* ?/gm, ''))
    return 0
  }
  if (!opts.target) throw new UsageError('缺少 --target <宿主项目根>')
  const target = path.resolve(opts.target)
  if (!fs.existsSync(path.join(target, 'package.json'))) throw new UsageError(`--target 不是宿主项目根（没有 package.json）：${target}`)

  if (opts.rollback) {
    restoreBackup(target, path.resolve(opts.rollback))
    console.log(`已按备份恢复：${path.resolve(opts.rollback)}`)
    return 0
  }

  const pkgDir = path.resolve(opts.package)
  const manifest = loadManifest(path.join(pkgDir, 'manifest.json'), '迁移包 manifest')
  verifyPackage(pkgDir, manifest)
  assertInsideTarget(target, manifest.managedRoot)
  if (opts.legacyScan) {
    legacyScan(target, manifest, opts.legacyDirs.length ? opts.legacyDirs : DEFAULT_LEGACY_DIRS)
    return 0
  }
  const statePath = hostPath(target, `${manifest.managedRoot}/${STATE_FILE}`)
  const state = isFile(statePath) ? loadManifest(statePath, '宿主同步状态') : null
  if (state && state.managedRoot !== manifest.managedRoot) throw new Error('迁移包与宿主同步状态的受管目录不一致')

  const plan = buildPlan(target, manifest, state)
  if (opts.json) console.log(JSON.stringify(plan, null, 2))
  else printPlan(plan, manifest, state, opts)
  const ignored = gitIgnoredPaths(target, manifest.files.map((e) => e.path).concat(`${manifest.managedRoot}/${STATE_FILE}`))
  warnGitIgnored(ignored)
  if (!opts.apply) return plan.conflicts.length ? 2 : 0

  const backupDir = applyPlan(target, pkgDir, manifest, state, plan, opts)
  if (backupDir) {
    console.log(`\n✅ 已应用。备份：${backupDir}`)
    console.log(`   回滚：node ${path.relative(process.cwd(), __filename) || __filename} --target ${target} --rollback ${backupDir}`)
    console.log('   请执行宿主构建与冒烟验证；依赖 / 合同有变化时先按上方提示处理。')
  }
  return 0
}

if (require.main === module) {
  try {
    process.exitCode = main(process.argv.slice(2))
  } catch (error) {
    console.error(`❌ ${error.message}`)
    process.exitCode = error instanceof ConflictError ? 2 : 1
  }
}

module.exports = { main, buildPlan, loadManifest, assertSafePath, STATE_FILE }
