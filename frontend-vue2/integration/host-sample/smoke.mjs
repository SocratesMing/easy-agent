#!/usr/bin/env node
/**
 * 宿主样例的浏览器冒烟（Node ≥ 22：内置 WebSocket；需要本机 Chrome / Chromium，无其他依赖）。
 * 经 Chrome DevTools 协议驱动无头浏览器，打开 mock-backend.cjs 托管的宿主样例，逐项断言：
 * 不抢宿主布局 / 标题 / URL、样式不互相污染、宿主 401 拦截与 window 事件不被触发、宿主 token
 * 与前缀不泄漏、流式对话与 markdown、离开 / 返回重新挂载、keep-alive 卸下全局监听、401 续期、
 * 被顶下线不互踢、Monaco worker / 字体 / 懒加载 chunk 在 /portal/ 基础路径下可加载。
 *
 *   node smoke.mjs --chrome <chrome 可执行文件> --base http://localhost:18091 [--out <截图目录>] [--skip-sdk]
 * 侧边栏 SDK 段需要把 frontend-vue2/dist-sdk 拷到宿主 dist/sdk/（与宿主页面同源加载），否则加 --skip-sdk。
 */
import { spawn } from 'child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'fs'
import { tmpdir } from 'os'
import { join } from 'path'

const argv = process.argv.slice(2)
const opt = (name, fallback) => {
  const i = argv.indexOf(name)
  return i === -1 ? fallback : argv[i + 1]
}
const CHROME = opt('--chrome')
const BASE = opt('--base', 'http://localhost:18091')
const OUT = opt('--out', mkdtempSync(join(tmpdir(), 'easy-agent-smoke-')))
const PORTAL = `${BASE}/portal/`
mkdirSync(OUT, { recursive: true })

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok: !!ok, detail })
  console.log(`${ok ? '✔' : '✖'} ${name}${detail ? ` —— ${detail}` : ''}`)
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function launch() {
  const profile = mkdtempSync(join(tmpdir(), 'easy-agent-chrome-'))
  const chrome = spawn(CHROME, ['--headless=new', '--no-sandbox', '--no-proxy-server', '--disable-gpu', '--window-size=1440,900',
    '--remote-debugging-port=0', `--user-data-dir=${profile}`, 'about:blank'], { stdio: ['ignore', 'ignore', 'pipe'] })
  const wsUrl = await new Promise((resolve, reject) => {
    let buf = ''
    chrome.stderr.on('data', (d) => {
      buf += d
      const m = /DevTools listening on (ws:\/\/\S+)/.exec(buf)
      if (m) resolve(m[1])
    })
    chrome.on('exit', () => reject(new Error(`Chrome 退出：${buf}`)))
  })
  const port = new URL(wsUrl).port
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()
  const page = targets.find((t) => t.type === 'page')
  return { chrome, wsUrl: page.webSocketDebuggerUrl }
}

function connect(wsUrl) {
  const ws = new WebSocket(wsUrl)
  let id = 0
  const pending = new Map()
  const listeners = []
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      if (msg.error) reject(new Error(msg.error.message))
      else resolve(msg.result)
    } else if (msg.method) {
      for (const fn of listeners) fn(msg)
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    id += 1
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  return new Promise((resolve) => {
    ws.onopen = () => resolve({ send, on: (fn) => listeners.push(fn), close: () => ws.close() })
  })
}

async function main() {
  const { chrome, wsUrl } = await launch()
  const cdp = await connect(wsUrl)
  const responses = []
  const consoleErrors = []
  cdp.on((msg) => {
    if (msg.method === 'Network.responseReceived') responses.push({ url: msg.params.response.url, status: msg.params.response.status, type: msg.params.type })
    if (msg.method === 'Runtime.exceptionThrown') consoleErrors.push(msg.params.exceptionDetails.text + ' ' + ((msg.params.exceptionDetails.exception || {}).description || ''))
    if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') consoleErrors.push(msg.params.args.map((a) => a.value || a.description || '').join(' '))
  })
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Network.enable')

  const evaluate = async (expression) => {
    const r = await cdp.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(`页面脚本出错：${r.exceptionDetails.text} ${(r.exceptionDetails.exception || {}).description || ''}`)
    return r.result.value
  }
  const waitFor = async (expression, timeout = 15000) => {
    const start = Date.now()
    while (Date.now() - start < timeout) {
      try {
        if (await evaluate(expression)) return true
      } catch (_) { /* 页面跳转中 */ }
      await sleep(150)
    }
    return false
  }
  const shot = async (name) => {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' })
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(data, 'base64'))
  }
  const go = async (hash) => {
    await evaluate(`location.hash = ${JSON.stringify(hash)}`)
    await sleep(300)
  }
  const mock = async (pathname, method = 'GET') => (await fetch(`${BASE}${pathname}`, { method })).json()
  const ctrlJ = () => evaluate(`(() => { const e = new KeyboardEvent('keydown', { key: 'j', ctrlKey: true, bubbles: true, cancelable: true }); window.dispatchEvent(e); return e.defaultPrevented })()`)

  // 宿主元素的计算样式（访问 Easy Agent 前后必须一致）
  const hostStyles = `(() => {
    const pick = (sel, props) => { const el = document.querySelector(sel); const cs = el && getComputedStyle(el); return el ? props.map((p) => cs.getPropertyValue(p)).join('|') : 'missing' }
    return {
      button: pick('.host-native-btn', ['padding-left', 'border-top-width', 'background-color']),
      elButton: pick('.host-page .el-button', ['background-color', 'border-top-color', 'padding-left']),
      list: pick('.host-list', ['list-style-type', 'padding-left']),
      h1: pick('.host-page h1', ['font-size', 'margin-top']),
      span: pick('.host-nav-note span', ['color']),
      code: pick('.host-page code.hljs', ['background-color', 'color']),
      body: pick('body', ['font-size', 'background-color']),
      rootVar: getComputedStyle(document.documentElement).getPropertyValue('--primary-color').trim(),
      htmlInline: document.documentElement.getAttribute('style') || '',
    }
  })()`

  await mock('/__mock/reset', 'POST')
  await cdp.send('Page.navigate', { url: `${PORTAL}#/home` })
  await waitFor(`!!document.querySelector('.host-page')`)
  const stylesBefore = await evaluate(hostStyles)
  await shot('01-host-home')

  // ── 进入 Easy Agent 页面 ──
  await go('#/easy-agent?keep=1')
  const rendered = await waitFor(`!!document.querySelector('.easy-agent-root .session-list') && !!document.querySelector('.easy-agent-root textarea')`, 20000)
  check('嵌入页渲染（会话列表 + 输入框）', rendered)
  await sleep(500)
  await shot('02-easy-agent')
  const layout = await evaluate(`(() => {
    const root = document.querySelector('.easy-agent-root').getBoundingClientRect()
    const content = document.querySelector('.host-content').getBoundingClientRect()
    const header = document.querySelector('.host-header').getBoundingClientRect()
    return { root: [root.left, root.top, root.width, root.height], content: [content.left, content.top, content.width, content.height], headerVisible: header.height > 0 && document.elementFromPoint(header.left + 30, header.top + 20).closest('.host-header') !== null, title: document.title, hash: location.hash, theme: document.querySelector('.easy-agent-root').getAttribute('data-theme'), exposed: typeof window.EasyAgentAI, rootFont: getComputedStyle(document.querySelector('.easy-agent-root')).fontSize }
  })()`)
  check('模块撑满宿主内容区、不越过宿主顶栏和侧栏', JSON.stringify(layout.root) === JSON.stringify(layout.content), `root=${layout.root} content=${layout.content}`)
  check('宿主顶栏仍可见可点', layout.headerVisible)
  check('不改 document.title', layout.title === '宿主门户', layout.title)
  check('不改宿主地址（hash 路由与无关 query 保留）', layout.hash === '#/easy-agent?keep=1', layout.hash)
  check('宿主 html 设了 data-theme=dark，模块仍是浅色（只看模块根节点）', layout.theme === 'light')
  check('嵌入模式不挂 window.EasyAgentAI', layout.exposed === 'undefined')
  check('模块根节点字号固定 16px（不继承宿主 body 14px）', layout.rootFont === '16px', layout.rootFont)

  const log1 = await mock('/__mock/log')
  const login = log1.find((e) => e.path === '/agent/auth/login-passwordless')
  check('宿主适配器用门户身份免密登录', !!login)
  check('Agent 请求不带宿主 token、不走宿主 axios baseURL', log1.every((e) => !e.hostToken && !e.hostPrefix), `${log1.length} 个请求`)
  const noToken = log1.filter((e) => /^\/(agent|api)\//.test(e.path) && !e.path.startsWith('/agent/auth/login') && !e.path.startsWith('/api/personnel/login-policy') && !/^Bearer mock-token-/.test(e.authorization))
  check('Agent 请求都带 Agent token（登录接口除外）', noToken.length === 0, noToken.map((e) => `${e.method} ${e.path} auth=${e.authorization || '(无)'}`).join('; '))
  const hostEvents = await evaluate('window.__hostEvents.slice()')
  check('不派发宿主同名 window 事件（auth-expired / user-activity）', hostEvents.length === 0, JSON.stringify(hostEvents))
  const hostTip = await evaluate(`document.querySelectorAll('.easy-agent-root [data-host-tip]').length`)
  check('模块内 v-tip 是局部指令，宿主同名全局指令没有生效到模块里', hostTip === 0, `命中 ${hostTip} 个`)

  // ── 流式对话 + markdown ──
  await evaluate(`document.querySelector('.easy-agent-root textarea').focus()`)
  await cdp.send('Input.insertText', { text: '你好' })
  await sleep(200)
  await evaluate(`document.querySelector('.easy-agent-root .send-btn').click()`)
  const replied = await waitFor(`[...document.querySelectorAll('.easy-agent-root .message-text')].some((el) => el.textContent.includes('mock 后端的流式回复'))`, 15000)
  check('SSE 流式回复逐段渲染', replied)
  await waitFor(`!!document.querySelector('.easy-agent-root .katex')`, 10000)
  const md = await evaluate(`(() => { const box = document.querySelector('.easy-agent-root .chat-messages, .easy-agent-root') ; return { strong: !!box.querySelector('.message-text strong'), code: !!box.querySelector('.message-text pre code'), katex: !!box.querySelector('.katex'), hljsBg: (() => { const c = box.querySelector('.message-text pre code'); return c ? getComputedStyle(c).backgroundColor : 'none' })() } })()`)
  check('Markdown：粗体、代码块、KaTeX 公式', md.strong && md.code && md.katex, JSON.stringify(md))
  await sleep(800)
  await shot('03-chat-reply')
  const fontsOk = await waitFor(`document.fonts.check('16px KaTeX_Main')`, 5000)
  const fontResponses = responses.filter((r) => /\/portal\/fonts\/KaTeX_/.test(r.url))
  check('KaTeX 字体从 /portal/fonts/ 加载', fontsOk && fontResponses.length > 0 && fontResponses.every((r) => r.status === 200), `${fontResponses.length} 个字体请求`)

  // ── 离开 / 返回：后端继续，前端重新挂载 ──
  await evaluate(`document.querySelector('.easy-agent-root textarea').focus()`)
  await cdp.send('Input.insertText', { text: '请慢一点回复' })
  await sleep(200)
  await evaluate(`document.querySelector('.easy-agent-root .send-btn').click()`)
  await waitFor(`[...document.querySelectorAll('.easy-agent-root .message-text')].some((el) => el.textContent.includes('这是一个'))`, 8000)
  const streamStateAtLeave = await evaluate(`sessionStorage.getItem('easy_agent_stream_state')`)
  await go('#/other?keep=1#frag')
  const left = await waitFor(`!document.querySelector('.easy-agent-root') && !!document.querySelector('.host-page')`)
  check('离开页面后模块 DOM 卸载', left)
  const otherRoute = await evaluate(`document.querySelector('.host-page p').textContent`)
  check('宿主路由的 query / hash 不受影响', otherRoute.includes('"keep":"1"') && otherRoute.includes('#frag'), otherRoute)
  const leakedLayers = await evaluate(`document.querySelectorAll('.ea-tip.show').length`)
  check('离开后没有残留的悬浮层', leakedLayers === 0)
  check('离开页面不触发 Ctrl+J（全局快捷键已移除）', (await ctrlJ()) === false)
  const logLeave = await mock('/__mock/log')
  check('离开页面不取消后端任务（没有 /agent/chat/cancel）', !logLeave.some((e) => e.path.startsWith('/agent/chat/cancel')))
  await sleep(1500)
  await go('#/easy-agent')
  const reattached = await waitFor(`[...document.querySelectorAll('.easy-agent-root .message-text')].some((el) => el.textContent.includes('后续内容'))`, 15000)
  const logBack = await mock('/__mock/log')
  const streamCalls = logBack.filter((e) => e.path.startsWith('/agent/chat/stream')).map((e) => e.path.split('?')[0])
  check('返回页面：按后端状态重新挂载进行中的回复并收到后续内容', reattached && streamCalls.includes('/agent/chat/stream/live'), `收到后续内容=${reattached} 流式调用=${streamCalls.join(',')} 离开时状态=${streamStateAtLeave}`)
  check('在页面内 Ctrl+J 可唤起 AI 侧边栏（监听已重新挂上）', (await ctrlJ()) === true)
  await shot('04-reattached')

  // ── keep-alive：缓存期间卸下全局监听，返回后实例继续 ──
  await go('#/easy-agent?keepalive=1')
  await waitFor(`!!document.querySelector('.easy-agent-root textarea')`)
  await evaluate(`window.__eaInstance = document.querySelector('.easy-agent-root').__vue__._uid`)
  await go('#/home?keepalive=1')
  await waitFor(`!document.querySelector('.easy-agent-root')`)
  check('keep-alive 缓存期间 Ctrl+J 不再被模块拦截', (await ctrlJ()) === false)
  await go('#/easy-agent?keepalive=1')
  await waitFor(`!!document.querySelector('.easy-agent-root textarea')`)
  check('keep-alive 返回复用同一实例', (await evaluate(`document.querySelector('.easy-agent-root').__vue__._uid === window.__eaInstance`)) === true)

  // ── 401 续期（这一段与下面「被顶下线」里的 401 报错是预期的，不计入意外报错） ──
  const errorsBeforeAuthTests = consoleErrors.length
  const loginsBefore = (await mock('/__mock/log')).filter((e) => e.path === '/agent/auth/login-passwordless').length
  await mock('/__mock/expire', 'POST')
  await evaluate(`document.querySelector('.easy-agent-root').__vue__.loadSessions()`)
  await sleep(800)
  const logRenew = await mock('/__mock/log')
  const loginsAfter = logRenew.filter((e) => e.path === '/agent/auth/login-passwordless').length
  const lastSessions = logRenew.filter((e) => e.path.split('?')[0] === '/agent/sessions' && e.method === 'GET').slice(-2)
  check('401 后自动重新执行 signIn 并重发原请求', loginsAfter === loginsBefore + 1 && lastSessions.length === 2 && lastSessions[0].status === 401 && !lastSessions[1].status, JSON.stringify(lastSessions.map((e) => e.status || 200)))
  check('续期过程没有触发宿主的 401 跳转', !(await evaluate('window.__hostEvents.slice()')).includes('host-401-redirect') && (await evaluate('location.hash')).startsWith('#/easy-agent'))

  // ── 被顶下线：不自动重登 ──
  await mock('/__mock/kick', 'POST')
  await evaluate(`document.querySelector('.easy-agent-root').__vue__.loadSessions()`)
  const blocked = await waitFor(`!!document.querySelector('.easy-agent-root .auth-blocked') && document.querySelector('.easy-agent-root .auth-blocked').textContent.includes('其他设备')`, 5000)
  const loginsKick = (await mock('/__mock/log')).filter((e) => e.path === '/agent/auth/login-passwordless').length
  check('被其他设备顶下线：显示提示，不自动重登（避免互踢）', blocked && loginsKick === loginsAfter)
  check('Agent 失效不影响宿主（没有跳宿主登录页、没有宿主 window 事件）', (await evaluate('window.__hostEvents.slice()')).length === 0 && (await evaluate('location.hash')).startsWith('#/easy-agent'))
  await shot('05-kicked')

  // ── Monaco worker（重新登录后打开 mcp.json 编辑器） ──
  const errorsAfterAuthTests = consoleErrors.length
  await mock('/__mock/reset', 'POST')
  // 只改 hash 不会重新加载页面：带上 query 强制整页重载（被顶下线后需要重新进入）
  await cdp.send('Page.navigate', { url: `${PORTAL}?reload=1#/easy-agent` })
  await waitFor(`!!document.querySelector('.easy-agent-root textarea')`, 20000)
  await evaluate(`document.querySelector('.easy-agent-root').__vue__.openSkillCenterPanel('connectors')`)
  await waitFor(`(() => { const find = (vm) => vm.$data && 'connectorView' in vm.$data ? vm : vm.$children.map(find).find(Boolean); return !!find(document.querySelector('.easy-agent-root').__vue__) })()`, 15000)
  await evaluate(`(() => { const find = (vm) => vm.$data && 'connectorView' in vm.$data ? vm : vm.$children.map(find).find(Boolean); find(document.querySelector('.easy-agent-root').__vue__).connectorView = 'manage' })()`)
  const editor = await waitFor(`!!document.querySelector('.easy-agent-root .monaco-editor .view-lines')`, 20000)
  await sleep(2500)
  const workerResponses = responses.filter((r) => r.type === 'Script' && /\/portal\/js\//.test(r.url) && responses.indexOf(r) >= 0)
  const workerErrors = consoleErrors.filter((t) => /worker/i.test(t))
  check('Monaco 编辑器在嵌入页渲染', editor)
  const workers = await evaluate(`performance.getEntriesByType('resource').filter((e) => e.initiatorType === 'other' || /worker/.test(e.name)).map((e) => e.name)`)
  check('Monaco worker 从 /portal/js/ 加载（非根路径部署）', workers.some((u) => u.includes('/portal/js/')) && workerErrors.length === 0, `${workers.join(', ')}${workerErrors.length ? ` 错误：${workerErrors.join(';')}` : ''}`)
  await shot('06-monaco')

  // ── 侧边栏 SDK 与嵌入页面共存：各自的 Vue 运行时与配置，token 分开存放，销毁 / 再初始化互不影响 ──
  if (!argv.includes('--skip-sdk')) {
    // 上一段停在技能中心（Monaco）：离开再进入，回到对话页
    await go('#/home')
    await waitFor(`!document.querySelector('.easy-agent-root')`)
    await go('#/easy-agent')
    await waitFor(`!!document.querySelector('.easy-agent-root .chat-container textarea, .easy-agent-root .input-field textarea')`)
    // 先让页面有一个会话：SDK destroy 会清它自己的会话列表，不能清到页面的
    await evaluate(`document.querySelector('.easy-agent-root textarea').focus()`)
    await cdp.send('Input.insertText', { text: '页面会话' })
    await evaluate(`document.querySelector('.easy-agent-root .send-btn').click()`)
    await waitFor(`document.querySelectorAll('.easy-agent-root .session-item').length > 0`, 10000)
    const pageSessionsBefore = await evaluate(`document.querySelectorAll('.easy-agent-root .session-item').length`)
    const pageToken = await evaluate(`localStorage.getItem('mini_agent_token')`)
    const loaded = await evaluate(`new Promise((resolve) => { const s = document.createElement('script'); s.src = ${JSON.stringify(`${PORTAL}sdk/easy-agent-sidebar.umd.min.js`)}; s.onload = () => resolve(!!window.EasyAgentSidebar); s.onerror = () => resolve(false); document.head.appendChild(s) })`)
    check('SDK UMD 与嵌入页面同页加载', loaded)
    const init = `window.__sdk = window.EasyAgentSidebar.init({ baseURL: ${JSON.stringify(BASE)}, auth: { username: 'sdk_user', userId: 'S1' }, theme: { '--primary-color': '#123456' }, features: { selectionAsk: false } }); window.__sdk.ready.then(() => true, () => false)`
    check('SDK 初始化成功', await evaluate(init))
    const sdk = await evaluate(`(() => {
      const host = document.querySelector('[data-easy-agent-sidebar]')
      const root = host && host.shadowRoot && host.shadowRoot.querySelector('.easy-agent-root')
      const ul = root && root.querySelector('ul')
      return { host: !!host, root: !!root, sidebar: !!(root && root.querySelector('.ais-root')), themeVar: root ? root.style.getPropertyValue('--primary-color') : '', sdkToken: localStorage.getItem('easy_agent_sdk_token'), pageToken: localStorage.getItem('mini_agent_token'), ulStyle: ul ? getComputedStyle(ul).listStyleType : 'no-ul', pageSessions: !!document.querySelector('.easy-agent-root .session-list') }
    })()`)
    check('SDK 挂在影子根里，模块根节点与侧边栏都在', sdk.host && sdk.root && sdk.sidebar)
    check('SDK 主题覆盖写在影子根内的模块根节点上', sdk.themeVar === '#123456', sdk.themeVar)
    check('SDK 与页面的 Agent token 分开存放、互不覆盖', !!sdk.sdkToken && sdk.pageToken === pageToken && sdk.sdkToken !== pageToken, `page=${pageToken} sdk=${sdk.sdkToken}`)
    const logSdk = await mock('/__mock/log')
    const sdkProfile = logSdk.filter((e) => e.path === '/agent/auth/profile').pop()
    check('SDK 请求带 SDK 自己的 token', !!sdkProfile && sdkProfile.authorization === `Bearer ${sdk.sdkToken}`)
    await evaluate(`document.querySelector('.easy-agent-root').__vue__.loadSessions()`)
    await sleep(300)
    const pageList = (await mock('/__mock/log')).filter((e) => e.path.split('?')[0] === '/agent/sessions' && e.method === 'GET').pop()
    check('SDK 加载后页面请求仍用页面自己的 token（没有被 SDK 的 configureRequest 覆盖）', pageList && pageList.authorization === `Bearer ${pageToken}`)
    await evaluate('window.__sdk.destroy()')
    const afterDestroy = await evaluate(`({ host: !!document.querySelector('[data-easy-agent-sidebar]'), page: !!document.querySelector('.easy-agent-root .session-list'), sessions: document.querySelectorAll('.easy-agent-root .session-item').length })`)
    check('SDK destroy 移除自身，不影响页面实例与会话列表', !afterDestroy.host && afterDestroy.page && pageSessionsBefore > 0 && afterDestroy.sessions === pageSessionsBefore, `${JSON.stringify(afterDestroy)} 之前 ${pageSessionsBefore} 个会话`)
    check('SDK 再次初始化成功', await evaluate(init))
    check('再次初始化后只有一个 SDK 实例', (await evaluate(`document.querySelectorAll('[data-easy-agent-sidebar]').length`)) === 1)
    await evaluate('window.__sdk.destroy()')
  }

  const failedAssets = responses.filter((r) => r.url.startsWith(PORTAL) && r.status >= 400)
  check('所有 /portal/ 下的 chunk、样式、字体请求成功', failedAssets.length === 0 && workerResponses.length > 0, failedAssets.map((r) => `${r.status} ${r.url}`).join(', '))
  const stylesAfter = await (async () => {
    await go('#/home')
    await waitFor(`!!document.querySelector('.host-page .host-native-btn')`)
    return evaluate(hostStyles)
  })()
  check('访问 Easy Agent 后宿主元素样式不变（模块 CSS 不泄漏）', JSON.stringify(stylesBefore) === JSON.stringify(stylesAfter), JSON.stringify({ before: stylesBefore, after: stylesAfter }))
  const unexpected = consoleErrors.slice(0, errorsBeforeAuthTests).concat(consoleErrors.slice(errorsAfterAuthTests))
  check('控制台没有意外报错', unexpected.length === 0, unexpected.slice(0, 5).join(' | '))

  writeFileSync(join(OUT, 'report.json'), JSON.stringify({ results, consoleErrors }, null, 2))
  cdp.close()
  chrome.kill()
  const failed = results.filter((r) => !r.ok)
  console.log(`\n${results.length - failed.length}/${results.length} 通过；截图与报告：${OUT}`)
  process.exit(failed.length ? 1 : 0)
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
