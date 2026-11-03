import assert from 'node:assert/strict'
import test from 'node:test'

import {
  RUNTIME_CONFIG_MARKER,
  parseRuntimeConfig,
  renderRuntimeConfig,
} from './runtime-config.mjs'

// 含引号、反斜杠、换行、CR、制表符等“会写坏 JS 字符串字面量”的值
const HOSTILE_TITLE = 'A "B" \\ C\n第二行\r\n\t制表符'
const HOSTILE_API = 'http://127.0.0.1:9000/pre"fix\\x\nnext\rline'

test('hostile 值经 JSON.stringify 转义后是合法 JS 片段，解析回来值精确不变', () => {
  const cfg = {
    AGENT_ENV: 'prod',
    API_BASE_URL: HOSTILE_API,
    APP_TITLE: HOSTILE_TITLE,
    APP_WELCOME_TITLE: `${HOSTILE_TITLE};window.__PWNED__ = true;//`,
    ENV_CONFIG: {
      dev: { API_BASE_URL: 'http://dev.local:8000' },
      test: { API_BASE_URL: '' },
      prod: { API_BASE_URL: 'http://prod.local:8000' },
    },
  }
  const text = renderRuntimeConfig(cfg)

  assert.ok(text.includes(`${RUNTIME_CONFIG_MARKER} = {`))
  assert.ok(text.includes('\\"'), '双引号应被转义')
  assert.ok(text.includes('\\\\'), '反斜杠应被转义')
  assert.ok(text.includes('\\n'), '换行应被转义')
  assert.ok(text.includes('\\r'), 'CR 应被转义')
  assert.ok(text.endsWith('};\n'))
  assert.deepEqual(parseRuntimeConfig(text), cfg)
})

test('parseRuntimeConfig 去掉前缀与结尾分号，兼容注释头；非法内容报错', () => {
  const cfg = {
    AGENT_ENV: 'dev',
    API_BASE_URL: '/',
    APP_TITLE: 'Easy Agent',
    APP_WELCOME_TITLE: '',
    ENV_CONFIG: {
      dev: { API_BASE_URL: '/' },
      test: { API_BASE_URL: '' },
      prod: { API_BASE_URL: '' },
    },
  }
  const text = renderRuntimeConfig(cfg)
  assert.deepEqual(parseRuntimeConfig(text), cfg)
  assert.deepEqual(parseRuntimeConfig(`// 额外注释\n\n${text}`), cfg)
  assert.deepEqual(parseRuntimeConfig(text.replace(/\n/g, '\r\n')), cfg)

  assert.throws(() => parseRuntimeConfig('window.foo = 1;'), /__RUNTIME_CONFIG__/)
  // dist 里未构建时的占位文件（public/runtime-config.js）不是 JSON，应报错而不是静默返回 {}
  assert.throws(
    () => parseRuntimeConfig('window.__RUNTIME_CONFIG__ = window.__RUNTIME_CONFIG__ || {}'),
    SyntaxError
  )
})
