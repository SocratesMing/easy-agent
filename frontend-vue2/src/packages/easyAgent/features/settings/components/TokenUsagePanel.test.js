import assert from 'node:assert/strict'
import test from 'node:test'
import { readFile } from 'node:fs/promises'
import * as helpers from '../../../shared/utils/tokenUsage.js'

const source = await readFile(new URL('./TokenUsagePanel.vue', import.meta.url), 'utf8')
const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '').replace('export default', 'return')
const helperNames = ['buildUsageChart', 'labelUsageSeries', 'formatTokens', 'formatExactTokens', 'validateUsageDays']

function harness() {
  let username = 'Alice', token = 'alice-token'
  const pending = [], listeners = new Set(), window = new EventTarget()
  const getTokenUsage = (days, signal) => new Promise((resolve, reject) => pending.push({ days, signal, resolve, reject }))
  const onRuntimeEvent = (name, listener) => { listeners.add(listener); return () => listeners.delete(listener) }
  const options = new Function('getTokenUsage', 'getStoredUsername', 'getStoredToken', 'onRuntimeEvent', 'window', ...helperNames, script)(
    getTokenUsage, () => username, () => token, onRuntimeEvent, window, ...helperNames.map(name => helpers[name]),
  )
  const vm = { ...options.data(), $nextTick: fn => Promise.resolve().then(fn) }
  for (const [name, method] of Object.entries(options.methods)) vm[name] = method.bind(vm)
  for (const [name, getter] of Object.entries(options.computed)) Object.defineProperty(vm, name, { get: getter.bind(vm) })
  options.mounted.call(vm)
  return { vm, pending, window, listeners,
    account(name, nextToken) {
      for (const listener of [...listeners]) listener()
      window.dispatchEvent(new Event('auth-changed'))
      username = name; token = nextToken
    },
    dispose() { (options.beforeDestroy || options.beforeUnmount).call(vm) },
  }
}

function fixture(days = 7, total = 1200) {
  return { days, as_of: '2026-10-30T04:00:00.000Z', timezone: 'Asia/Shanghai',
    summary: { today: total, last_7_days: 7000, last_30_days: 17300, all_time: 32000 },
    daily: {
      dates: Array.from({ length: days }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}`),
      totals: Array.from({ length: days }, (_, i) => i === days - 1 ? total : 0),
      series: [{ model_key: 'default', model_name: 'A', values: Array.from({ length: days }, (_, i) => i === days - 1 ? total : 0) }],
    },
  }
}
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve() }

test('range changes discard prior results and hide the old chart while loading', async () => {
  const h = harness()
  assert.equal(h.pending[0].days, 7)
  h.vm.changeDays(30)
  assert.equal(h.pending[0].signal.aborted, true)
  assert.equal(h.vm.usage, null)
  assert.equal(h.vm.loading, true)
  h.pending[1].resolve(fixture(30))
  await flush()
  h.pending[0].resolve(fixture(7, 999999))
  await flush()
  assert.equal(h.vm.usage.days, 30)
  assert.equal(h.vm.usage.summary.today, 1200)
  assert.equal(h.vm.loading, false)
  assert.equal(h.vm.selectedIndex, 29)
  h.dispose()
})

test('refresh failures show retry state; retry and empty data remain usable', async () => {
  const h = harness()
  h.pending[0].reject(new Error('Token统计暂不可用，请重试'))
  await flush()
  assert.match(h.vm.error, /请重试/)
  assert.equal(h.vm.loading, false)
  const retry = h.vm.loadUsage()
  h.pending[1].resolve(fixture(7, 0))
  await retry
  assert.equal(h.vm.error, '')
  assert.equal(h.vm.hasWindowUsage, false)
  assert.equal(h.vm.chart.ticks[0].value, 0)
  h.dispose()
})

test('account change clears data immediately and ignores the old account response', async () => {
  const h = harness()
  h.pending[0].resolve(fixture())
  await flush()
  const refresh = h.vm.loadUsage()
  h.account('Bob', 'bob-token')
  assert.equal(h.vm.usage, null)
  await flush()
  assert.equal(h.pending.length, 3)
  h.pending[1].resolve(fixture(7, 999999))
  await refresh
  assert.equal(h.vm.usage, null)
  h.pending[2].resolve(fixture(7, 100000))
  await flush()
  assert.equal(h.vm.usage.summary.today, 100000)
  h.dispose()
})

test('logout and unmount abort requests, detach listeners and reject late responses', async () => {
  const h = harness()
  h.account(null, null)
  await flush()
  assert.equal(h.pending.length, 1)
  assert.equal(h.pending[0].signal.aborted, true)
  assert.match(h.vm.error, /请登录/)
  h.pending[0].resolve(fixture())
  await flush()
  assert.equal(h.vm.usage, null)
  h.account('Bob', 'bob-token')
  await flush()
  h.dispose()
  assert.equal(h.listeners.size, 0)
  assert.equal(h.pending[1].signal.aborted, true)
  h.pending[1].resolve(fixture())
  await flush()
  assert.equal(h.vm.usage, null)
  h.account('Charlie', 'charlie-token')
  await flush()
  assert.equal(h.pending.length, 2)
})

test('legend filtering preserves exact totals and every model in detail data', async () => {
  const h = harness()
  h.pending[0].resolve(fixture())
  await flush()
  h.vm.toggleSeries(h.vm.series[0].id)
  assert.equal(h.vm.chart.series.length, 0)
  assert.equal(h.vm.series.length, 1)
  assert.equal(h.vm.usage.daily.totals[6], 1200)
  assert.equal(h.vm.exact(123456789), '123,456,789')
  assert.match(source, /<details/)
  assert.match(source, /role="button"[^>]*@focus=/)
  assert.match(source, /@keydown.enter.prevent=/)
  assert.doesNotMatch(source, /v-html/)
  h.dispose()
})
