import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import { JSDOM } from 'jsdom'
import * as helpers from '../../../shared/utils/tokenUsage.js'

test('Vue2 compiles and mounts cards, SVG, keyboard details and legend without render warnings', async () => {
  const dom = new JSDOM('<html><body><div id="mount"></div></body></html>')
  const keys = ['window', 'document', 'navigator', 'Element', 'HTMLElement', 'SVGElement', 'Node']
  const originals = keys.map(key => Object.getOwnPropertyDescriptor(globalThis, key))
  for (const key of keys) Object.defineProperty(globalThis, key, { value: dom.window[key], configurable: true, writable: true })
  const require = createRequire(import.meta.url)
  const Vue = require('vue/dist/vue.common.js')
  const oldError = Vue.config.errorHandler, oldWarn = Vue.config.warnHandler
  const errors = []
  Vue.config.errorHandler = error => errors.push(error)
  Vue.config.warnHandler = warning => errors.push(warning)
  let vm
  try {
    const source = await readFile(new URL('./TokenUsagePanel.vue', import.meta.url), 'utf8')
    const template = source.slice(source.indexOf('<template>') + 10, source.indexOf('\n</template>'))
    const script = source.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/^import .*$/gm, '').replace('export default', 'return')
    const names = ['buildUsageChart', 'labelUsageSeries', 'formatTokens', 'formatExactTokens', 'validateUsageDays']
    const usage = { days: 7, as_of: '2026-10-30T04:00:00.000Z',
      summary: { today: 1200, last_7_days: 7000, last_30_days: 17300, all_time: 32000 },
      daily: { dates: ['2026-10-24', '2026-10-25', '2026-10-26', '2026-10-27', '2026-10-28', '2026-10-29', '2026-10-30'],
        totals: [3500, 0, 0, 0, 0, 2300, 1200],
        series: [{ model_key: 'default', model_name: 'deepseek-flash', values: [3500, 0, 0, 0, 0, 0, 1200] },
          { model_key: 'secondary', model_name: 'model-b', values: [0, 0, 0, 0, 0, 2300, 0] }] },
    }
    const options = new Function('getTokenUsage', 'getStoredUsername', 'getStoredToken', 'onRuntimeEvent', ...names, script)(
      async () => usage, () => 'Alice', () => 'token', () => () => {}, ...names.map(name => helpers[name]),
    )
    Object.assign(options, Vue.compile(template))
    vm = new Vue(options).$mount('#mount')
    await Promise.resolve(); await Vue.nextTick()
    assert.deepEqual([...vm.$el.querySelectorAll('.usage-card strong')].map(element => element.textContent), ['1,200', '7,000', '1.73万', '3.2万'])
    assert.equal(vm.$el.querySelectorAll('.date-hit').length, 7)
    assert.equal(vm.$el.querySelectorAll('tbody tr').length, 7)
    vm.$el.querySelector('.date-hit').dispatchEvent(new dom.window.FocusEvent('focus'))
    await Vue.nextTick()
    assert.match(vm.$el.querySelector('.usage-tooltip').textContent, /2026-10-24.*总Token：3,500/)
    vm.$el.querySelector('.usage-legend button').click()
    await Vue.nextTick()
    assert.equal(vm.$el.querySelectorAll('.chart-line').length, 1)
    assert.match(vm.$el.querySelector('.usage-tooltip').textContent, /deepseek-flash：3,500/)
    assert.deepEqual(errors, [])
  } finally {
    if (vm) vm.$destroy()
    Vue.config.errorHandler = oldError; Vue.config.warnHandler = oldWarn
    dom.window.close()
    keys.forEach((key, index) => {
      if (originals[index]) Object.defineProperty(globalThis, key, originals[index])
      else delete globalThis[key]
    })
  }
})
