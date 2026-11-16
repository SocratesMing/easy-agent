/**
 * 业务模块边界检查、迁移包导出、导航与运行时配置的测试。
 */
import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, readdirSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { tmpdir } from 'node:os'
import path from 'node:path'
import test from 'node:test'

const FRONTEND = path.resolve(import.meta.dirname, '..')
const { checkFile } = await import('../scripts/easy-agent/check.mjs')
const { diffDependencies, diffFiles } = await import('../scripts/easy-agent/export.mjs')
const { applyQueryPatch, buildLocationWithQuery, createVueRouterNavigation, readQuery } = await import('../src/packages/easyAgent/runtime/navigation.js')
const { configureEasyAgent, getRuntime, resetEasyAgentConfig } = await import('../src/packages/easyAgent/runtime/config.js')

const ctx = { deps: { vue: {}, 'element-ui': {} }, pkgDeps: { vue: '^2.7.16', 'element-ui': '^2.15.14' }, exists: () => true }

test('check：模块真实源码通过（npm run check:easy-agent 退出码 0）', () => {
  const r = spawnSync(process.execPath, [path.join(FRONTEND, 'scripts/easy-agent/check.mjs')], { encoding: 'utf8' })
  assert.equal(r.status, 0, r.stdout + r.stderr)
})

test('check：宿主 alias、特殊加载器、越界相对路径、未声明依赖、Node 内置模块都会失败', () => {
  const code = [
    "import request from '@/utils/request'",
    "import Icon from '~icons/lucide/x'",
    "import host from '../../../../api/host.js'",
    "import _ from 'lodash'",
    "import fs from 'node:fs'",
    "const Panel = () => import('@/pages/x.vue')",
    "new Worker(new URL('/abs/worker.js', import.meta.url))",
  ].join('\n')
  const problems = checkFile('features/x.js', code, ctx).join('\n')
  for (const needle of ['@/utils/request', '~icons/lucide/x', '逃逸出模块目录', '未在 dependencies.json 声明的包 lodash', 'node:fs', '@/pages/x.vue', '绝对路径 /abs/worker.js']) {
    assert.ok(problems.includes(needle), `缺少：${needle}\n${problems}`)
  }
})

test('check：全局注册、$mount、宿主私有存储键、改 history / html 都会失败；注释里的示例不算', () => {
  const code = [
    "// Vue.use(Something) 只是注释",
    "Vue.directive('tip', tip)",
    "new Vue({}).$mount('#x')",
    "localStorage.getItem('system:share:loginId')",
    "window.history.replaceState({}, '', '/')",
    "document.documentElement.style.setProperty('--x', 1)",
    "window.dispatchEvent(new CustomEvent('auth-expired'))",
  ].join('\n')
  const problems = checkFile('features/y.js', code, ctx)
  assert.equal(problems.length, 6, problems.join('\n'))
})

test('check：html / :root 选择器与未限域的非 scoped 样式会失败，限域写法通过', () => {
  const bad = '<template><div /></template>\n<style scoped>\nhtml[data-theme="dark"] .a { color: red }\n</style>\n<style>\n.global-leak { color: red }\n</style>'
  const problems = checkFile('features/z.vue', bad, ctx).join('\n')
  assert.match(problems, /html \/ body \/ :root/)
  assert.match(problems, /未限定在模块根节点内：\.global-leak/)
  const good = '<template><div /></template>\n<style>\n:where(.easy-agent-root) .a, .easy-agent-root[data-theme="dark"] .b { color: red }\n@keyframes spin { to { transform: rotate(1turn) } }\n</style>'
  assert.deepEqual(checkFile('features/ok.vue', good, ctx), [])
})

test('export：manifest 逐文件哈希与包内文件一致，不含测试文件，附带接入模板与同步工具', () => {
  const out = mkdtempSync(path.join(tmpdir(), 'easy-agent-export-'))
  try {
    const r = spawnSync(process.execPath, [path.join(FRONTEND, 'scripts/easy-agent/export.mjs'), '--out', out, '--no-check'], { encoding: 'utf8' })
    assert.equal(r.status, 0, r.stdout + r.stderr)
    const [name] = readdirSync(out)
    const pkg = path.join(out, name)
    const manifest = JSON.parse(readFileSync(path.join(pkg, 'manifest.json'), 'utf8'))
    assert.equal(manifest.managedRoot, 'src/packages/easyAgent')
    assert.equal(manifest.baseline, null)
    assert.ok(manifest.files.length > 50)
    for (const entry of manifest.files) {
      assert.ok(!entry.path.endsWith('.test.js'), entry.path)
      const content = readFileSync(path.join(pkg, 'module', entry.path))
      assert.equal(createHash('sha256').update(content).digest('hex'), entry.sha256, entry.path)
    }
    for (const required of ['integration/tools/sync-easy-agent.cjs', 'integration/host-template/src/integrations/easyAgent.js', 'integration/host-template/src/pages/easyAgent/index.vue', 'CHANGES.md']) {
      assert.ok(existsSync(path.join(pkg, required)), required)
    }
    assert.ok(manifest.dependencies.vue && manifest.dependencies.axios)

    // 以这一版为基线再导出一次：没有变化
    const again = spawnSync(process.execPath, [path.join(FRONTEND, 'scripts/easy-agent/export.mjs'), '--out', path.join(out, 'next'), '--no-check', '--baseline', path.join(pkg, 'manifest.json')], { encoding: 'utf8' })
    assert.equal(again.status, 0, again.stdout + again.stderr)
    const [nextName] = readdirSync(path.join(out, 'next'))
    const next = JSON.parse(readFileSync(path.join(out, 'next', nextName, 'manifest.json'), 'utf8'))
    assert.deepEqual(next.changes, { added: [], modified: [], deleted: [] })
    assert.equal(next.contractChanged, null)
  } finally {
    rmSync(out, { recursive: true, force: true })
  }
})

test('export：新增 / 修改 / 删除与依赖变化按基线计算', () => {
  const before = [{ path: 'a', sha256: '1' }, { path: 'b', sha256: '2' }, { path: 'c', sha256: '3' }]
  const after = [{ path: 'a', sha256: '1' }, { path: 'b', sha256: '9' }, { path: 'd', sha256: '4' }]
  assert.deepEqual(diffFiles(before, after), { added: ['d'], modified: ['b'], deleted: ['c'] })
  const deps = diffDependencies({ vue: { range: '^2.7.16', locked: '2.7.16' }, old: { range: '1', locked: '1' } }, { vue: { range: '^2.7.16', locked: '2.7.17' }, marked: { range: '^17', locked: '17.0.6' } })
  assert.deepEqual(Object.keys(deps.added), ['marked'])
  assert.deepEqual(Object.keys(deps.removed), ['old'])
  assert.deepEqual(Object.keys(deps.changed), ['vue'])
})

test('navigation：只改指定的 query 键，保留 path、hash 与其他参数', () => {
  const loc = { pathname: '/portal/', search: '?a=1&session=s1&panel=skills', hash: '#/easy-agent?x=1' }
  assert.equal(buildLocationWithQuery(loc, { session: null, panel: null }), '/portal/?a=1#/easy-agent?x=1')
  assert.deepEqual(applyQueryPatch({ a: '1', session: 's' }, { session: null, b: 2 }), { a: '1', b: '2' })
})

test('navigation：vue-router 实现读写 $route.query，NavigationDuplicated 被吞掉', async () => {
  const calls = []
  const vm = {
    $route: { path: '/easy-agent', query: { session: ['s1', 's2'], keep: '1' }, hash: '#frag' },
    $router: { replace: (loc) => { calls.push(loc); return Promise.reject(new Error('NavigationDuplicated')) } },
  }
  resetEasyAgentConfig()
  configureEasyAgent({ mode: 'embedded', navigation: createVueRouterNavigation() })
  assert.deepEqual(readQuery(vm), { session: 's1', keep: '1' })
  await getRuntime().navigation.replaceQuery(vm, { session: null })
  assert.deepEqual(calls, [{ path: '/easy-agent', query: { keep: '1' }, hash: '#frag' }])
})

test('navigation：嵌入模式没配 navigation 时不读 location，也不改 history', () => {
  resetEasyAgentConfig()
  configureEasyAgent({ mode: 'embedded' })
  globalThis.window = { location: { search: '?session=leak', pathname: '/', hash: '' }, history: { replaceState: () => { throw new Error('不应调用') } } }
  try {
    assert.deepEqual(readQuery({}), {})
  } finally {
    delete globalThis.window
  }
})

test('runtime：按模式给默认值，校验 mode，未知字段只告警，重复配置合并而不丢字段', () => {
  resetEasyAgentConfig()
  configureEasyAgent({ mode: 'embedded', api: { baseURL: 'https://agent.example.com/' } })
  const rt = getRuntime()
  assert.equal(rt.mode, 'embedded')
  assert.equal(rt.api.baseURL, 'https://agent.example.com')
  assert.equal(rt.auth.loginPage, false)
  assert.equal(rt.app.setDocumentTitle, false)
  assert.equal(rt.features.exposeGlobal, false)
  configureEasyAgent({ app: { title: '宿主标题' } })
  assert.equal(getRuntime().api.baseURL, 'https://agent.example.com')
  assert.equal(getRuntime().app.title, '宿主标题')
  assert.throws(() => configureEasyAgent({ mode: 'iframe' }), /mode 只能是/)
  const warnings = []
  const original = console.warn
  console.warn = (m) => warnings.push(m)
  try {
    configureEasyAgent({ auth: { tokn: 'x' }, unknownSection: {} })
  } finally {
    console.warn = original
  }
  assert.equal(warnings.length, 2)
  resetEasyAgentConfig()
  assert.equal(getRuntime().mode, 'standalone')
  assert.equal(getRuntime().auth.loginPage, true)
})

test('runtime：页面根节点主题只写模块根，不碰 html（浅色默认值在 base.css 上）', () => {
  const css = readFileSync(path.join(FRONTEND, 'src/packages/easyAgent/styles/base.css'), 'utf8')
  assert.match(css, /\.easy-agent-root \{\n  --primary-color: #0ea5e9;/)
  const theme = readFileSync(path.join(FRONTEND, 'src/packages/easyAgent/runtime/theme.js'), 'utf8')
  assert.doesNotMatch(theme.replace(/\/\/.*$/gm, ''), /documentElement/)
})
