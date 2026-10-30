/**
 * 内网同步工具（integration/tools/sync-easy-agent.cjs）的行为测试：在临时目录里造迁移包与宿主，
 * 以子进程运行（与内网用法一致），覆盖新增 / 修改 / 删除 / 冲突 / 强制覆盖 / 重复应用 / 失败恢复 /
 * 回滚 / 路径越界 / 包被篡改 / 未纳管文件与宿主其他文件保持不变。
 */
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync, readdirSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'

const TOOL = path.resolve(import.meta.dirname, '../integration/tools/sync-easy-agent.cjs')
const ROOT = 'src/packages/easyAgent'
const sha = (s) => createHash('sha256').update(s).digest('hex')

function makeWorkspace() {
  const dir = mkdtempSync(path.join(tmpdir(), 'easy-agent-sync-'))
  const host = path.join(dir, 'host')
  mkdirSync(path.join(host, 'src', 'pages', 'easyAgent'), { recursive: true })
  writeFileSync(path.join(host, 'package.json'), '{"name":"host"}\n')
  writeFileSync(path.join(host, 'src', 'pages', 'easyAgent', 'index.vue'), '<template><div>宿主页面包装</div></template>\n')
  writeFileSync(path.join(host, 'src', 'main.js'), 'console.log("host main")\n')
  return { dir, host }
}

/** files: { 相对受管目录的路径: 内容 } */
function makePackage(dir, name, files, extra = {}) {
  const pkg = path.join(dir, name)
  const entries = []
  for (const [rel, content] of Object.entries(files)) {
    const p = `${ROOT}/${rel}`
    const file = path.join(pkg, 'module', p)
    mkdirSync(path.dirname(file), { recursive: true })
    writeFileSync(file, content)
    entries.push({ path: p, sha256: sha(content), size: Buffer.byteLength(content) })
  }
  const manifest = {
    schemaVersion: 1,
    package: 'easy-agent',
    managedRoot: ROOT,
    contractVersion: extra.contractVersion || 1,
    generatedAt: extra.generatedAt || new Date().toISOString(),
    source: { git: null, note: '测试包' },
    dependencies: extra.dependencies || { vue: { range: '^2.7.16', locked: '2.7.16' } },
    files: entries,
    ...(extra.manifest || {}),
  }
  writeFileSync(path.join(pkg, 'manifest.json'), JSON.stringify(manifest, null, 2))
  return pkg
}

// 工具本身要在内网 Node 14 上跑：EASY_AGENT_SYNC_NODE=<node14 路径> 时用它执行（测试框架仍用当前 Node）
const TOOL_NODE = process.env.EASY_AGENT_SYNC_NODE || process.execPath

function run(args, env = {}) {
  const result = spawnSync(TOOL_NODE, [TOOL, ...args], { encoding: 'utf8', env: { ...process.env, ...env } })
  return { status: result.status, out: result.stdout + result.stderr }
}

const read = (host, rel) => readFileSync(path.join(host, ROOT, rel), 'utf8')
const has = (host, rel) => existsSync(path.join(host, ROOT, rel))

test('首次导入：预览不写文件，应用后文件与状态齐全，宿主其他文件不变', () => {
  const { dir, host } = makeWorkspace()
  try {
    const pkg = makePackage(dir, 'v1', { 'index.js': 'export const a = 1\n', 'features/a.vue': '<template><i /></template>\n' })
    const preview = run(['--package', pkg, '--target', host])
    assert.equal(preview.status, 0, preview.out)
    assert.match(preview.out, /新增（2）/)
    assert.equal(has(host, 'index.js'), false)

    const applied = run(['--package', pkg, '--target', host, '--apply'])
    assert.equal(applied.status, 0, applied.out)
    assert.equal(read(host, 'index.js'), 'export const a = 1\n')
    assert.ok(has(host, '.easy-agent-sync.json'))
    assert.equal(readFileSync(path.join(host, 'src/pages/easyAgent/index.vue'), 'utf8'), '<template><div>宿主页面包装</div></template>\n')
    assert.equal(readFileSync(path.join(host, 'src/main.js'), 'utf8'), 'console.log("host main")\n')

    const again = run(['--package', pkg, '--target', host, '--apply'])
    assert.equal(again.status, 0, again.out)
    assert.match(again.out, /无变化/)
    assert.equal(existsSync(path.join(host, '.easy-agent-backups')) && readdirSync(path.join(host, '.easy-agent-backups')).length, 1)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('升级：修改与新增照常写入，上一版受管且未改动的文件被删除，未纳管文件保留', () => {
  const { dir, host } = makeWorkspace()
  try {
    run(['--package', makePackage(dir, 'v1', { 'index.js': 'v1\n', 'old.js': 'old\n', 'keep.js': 'keep\n' }), '--target', host, '--apply'])
    writeFileSync(path.join(host, ROOT, 'local-note.md'), '宿主自己放的文件\n')
    const v2 = makePackage(dir, 'v2', { 'index.js': 'v2\n', 'keep.js': 'keep\n', 'new/added.js': 'added\n' }, { dependencies: { vue: { range: '^2.7.16', locked: '2.7.16' }, marked: { range: '^17.0.6', locked: '17.0.6' } }, contractVersion: 2 })
    const preview = run(['--package', v2, '--target', host])
    assert.match(preview.out, /依赖有变化[\s\S]*新增 marked@\^17\.0\.6/)
    assert.match(preview.out, /适配合同版本 1 → 2/)
    assert.match(preview.out, /未纳管文件[\s\S]*local-note\.md/)
    const applied = run(['--package', v2, '--target', host, '--apply'])
    assert.equal(applied.status, 0, applied.out)
    assert.equal(read(host, 'index.js'), 'v2\n')
    assert.equal(read(host, 'new/added.js'), 'added\n')
    assert.equal(has(host, 'old.js'), false)
    assert.equal(read(host, 'local-note.md'), '宿主自己放的文件\n')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('冲突：宿主改过的受管文件默认不覆盖、不删除，退出码 2 且未写入任何文件', () => {
  const { dir, host } = makeWorkspace()
  try {
    run(['--package', makePackage(dir, 'v1', { 'index.js': 'v1\n', 'gone.js': 'gone\n' }), '--target', host, '--apply'])
    writeFileSync(path.join(host, ROOT, 'index.js'), '内网定制\n')
    writeFileSync(path.join(host, ROOT, 'gone.js'), '内网也改了\n')
    const v2 = makePackage(dir, 'v2', { 'index.js': 'v2\n', 'other.js': 'other\n' })
    const applied = run(['--package', v2, '--target', host, '--apply'])
    assert.equal(applied.status, 2, applied.out)
    assert.match(applied.out, /index\.js —— 宿主本地修改过该受管文件/)
    assert.match(applied.out, /gone\.js —— 上一版受管文件已被宿主修改/)
    assert.equal(read(host, 'index.js'), '内网定制\n')
    assert.equal(has(host, 'other.js'), false)
    assert.equal(read(host, 'gone.js'), '内网也改了\n')
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('同路径已有未纳管文件（首次导入）也按冲突处理；--force 覆盖前先备份，可回滚', () => {
  const { dir, host } = makeWorkspace()
  try {
    mkdirSync(path.join(host, ROOT), { recursive: true })
    writeFileSync(path.join(host, ROOT, 'index.js'), '早先手工拷贝的版本\n')
    const pkg = makePackage(dir, 'v1', { 'index.js': 'v1\n', 'b.js': 'b\n' })
    assert.equal(run(['--package', pkg, '--target', host, '--apply']).status, 2)
    const backupDir = path.join(dir, 'backup')
    const forced = run(['--package', pkg, '--target', host, '--apply', '--force', '--backup-dir', backupDir])
    assert.equal(forced.status, 0, forced.out)
    assert.equal(read(host, 'index.js'), 'v1\n')

    const rolled = run(['--target', host, '--rollback', backupDir])
    assert.equal(rolled.status, 0, rolled.out)
    assert.equal(read(host, 'index.js'), '早先手工拷贝的版本\n')
    assert.equal(has(host, 'b.js'), false)
    assert.equal(has(host, '.easy-agent-sync.json'), false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('写入后失败：自动按备份恢复，不写入新的同步状态', () => {
  const { dir, host } = makeWorkspace()
  try {
    run(['--package', makePackage(dir, 'v1', { 'index.js': 'v1\n', 'old.js': 'old\n' }), '--target', host, '--apply'])
    const stateBefore = read(host, '.easy-agent-sync.json')
    const v2 = makePackage(dir, 'v2', { 'index.js': 'v2\n', 'new.js': 'new\n' })
    const failed = run(['--package', v2, '--target', host, '--apply'], { EASY_AGENT_SYNC_FAIL_AFTER_WRITE: '1' })
    assert.equal(failed.status, 1, failed.out)
    assert.match(failed.out, /正在按备份恢复/)
    assert.equal(read(host, 'index.js'), 'v1\n')
    assert.equal(read(host, 'old.js'), 'old\n')
    assert.equal(has(host, 'new.js'), false)
    assert.equal(read(host, '.easy-agent-sync.json'), stateBefore)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('拒绝越界路径、被篡改的包与不完整的清单', () => {
  const { dir, host } = makeWorkspace()
  try {
    const escape = makePackage(dir, 'escape', { 'index.js': 'x\n' }, { manifest: {} })
    const manifest = JSON.parse(readFileSync(path.join(escape, 'manifest.json'), 'utf8'))
    manifest.files.push({ path: `${ROOT}/../../main.js`, sha256: sha('evil'), size: 4 })
    writeFileSync(path.join(escape, 'manifest.json'), JSON.stringify(manifest))
    const r1 = run(['--package', escape, '--target', host, '--apply'])
    assert.equal(r1.status, 1)
    assert.match(r1.out, /路径不规范或越界/)

    const tampered = makePackage(dir, 'tampered', { 'index.js': 'x\n' })
    writeFileSync(path.join(tampered, 'module', ROOT, 'index.js'), 'changed\n')
    const r2 = run(['--package', tampered, '--target', host, '--apply'])
    assert.equal(r2.status, 1)
    assert.match(r2.out, /校验失败/)

    const extra = makePackage(dir, 'extra', { 'index.js': 'x\n' })
    writeFileSync(path.join(extra, 'module', ROOT, 'undeclared.js'), 'y\n')
    const r3 = run(['--package', extra, '--target', host, '--apply'])
    assert.equal(r3.status, 1)
    assert.match(r3.out, /未声明的文件/)

    const outside = makePackage(dir, 'outside', { 'index.js': 'x\n' })
    const m = JSON.parse(readFileSync(path.join(outside, 'manifest.json'), 'utf8'))
    m.files[0].path = 'src/main.js'
    writeFileSync(path.join(outside, 'manifest.json'), JSON.stringify(m))
    const r4 = run(['--package', outside, '--target', host, '--apply'])
    assert.equal(r4.status, 1)
    assert.match(r4.out, /不在受管目录/)
    assert.equal(readFileSync(path.join(host, 'src/main.js'), 'utf8'), 'console.log("host main")\n')
    assert.equal(has(host, 'index.js'), false)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('旧目录扫描：只报告同内容 / 同名文件与仍然引用旧路径的位置，不删除任何文件', () => {
  const { dir, host } = makeWorkspace()
  try {
    const pkg = makePackage(dir, 'v1', { 'api/chat.js': 'export const chat = 1\n', 'utils/format.js': 'export const f = 1\n' })
    mkdirSync(path.join(host, 'src/api/easyAgent'), { recursive: true })
    writeFileSync(path.join(host, 'src/api/easyAgent/chat.js'), 'export const chat = 1\n')
    writeFileSync(path.join(host, 'src/api/easyAgent/format.js'), '内网改过\n')
    writeFileSync(path.join(host, 'src/views.js'), "import { chat } from '@/api/easyAgent/chat'\n")
    const r = run(['--package', pkg, '--target', host, '--legacy-scan'])
    assert.equal(r.status, 0, r.out)
    assert.match(r.out, /src\/api\/easyAgent\/chat\.js —— 内容与 src\/packages\/easyAgent\/api\/chat\.js 完全相同/)
    assert.match(r.out, /format\.js —— 同名/)
    assert.match(r.out, /src\/views\.js:1/)
    assert.ok(existsSync(path.join(host, 'src/api/easyAgent/chat.js')))
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})

test('宿主 .gitignore 忽略了受管文件时醒目提示（如通用的 workspace/ 规则会漏提交 features/workspace/）', () => {
  const { dir, host } = makeWorkspace()
  try {
    execFileSync('git', ['init', '-q', host])
    writeFileSync(path.join(host, '.gitignore'), 'workspace/\n')
    const pkg = makePackage(dir, 'v1', { 'index.js': 'x\n', 'features/workspace/Panel.vue': '<template><i /></template>\n' })
    const r = run(['--package', pkg, '--target', host, '--apply'])
    assert.equal(r.status, 0, r.out)
    assert.match(r.out, /1 个受管文件被宿主 \.gitignore 忽略[\s\S]*features\/workspace\/Panel\.vue/)
    writeFileSync(path.join(host, '.gitignore'), 'workspace/\n!src/packages/easyAgent/**\n!src/packages/easyAgent/features/workspace/\n')
    const again = run(['--package', pkg, '--target', host])
    assert.doesNotMatch(again.out, /被宿主 \.gitignore 忽略/)
  } finally {
    rmSync(dir, { recursive: true, force: true })
  }
})
