import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'

import {
  RUNTIME_CONFIG_MARKER,
  parseRuntimeConfig,
  renderRuntimeConfig,
} from './runtime-config.mjs'
import { configFromEnv, writeRuntimeConfig } from './write-runtime-config.mjs'

// 含引号、反斜杠、换行、CR、制表符等“会写坏 JS 字符串字面量”的值
const HOSTILE_TITLE = 'A "B" \\ C\n第二行\r\n\t制表符'
const HOSTILE_API = 'http://127.0.0.1:9000/pre"fix\\x\nnext\rline'

// 每个用例单独建临时目录，结束后删除，不往仓库里留产物
function makeTempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'easy-agent-runtime-config-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}

test('hostile 值经 JSON.stringify 转义后是合法 JS，解析回来值精确不变', (t) => {
  const dir = makeTempDir(t)
  const file = path.join(dir, 'runtime-config.js')
  const welcome = `${HOSTILE_TITLE};window.__PWNED__ = true;//`
  const text = writeRuntimeConfig(file, {
    RT_AGENT_ENV: 'prod',
    RT_API_BASE_URL: HOSTILE_API,
    RT_APP_TITLE: HOSTILE_TITLE,
    RT_APP_WELCOME_TITLE: welcome,
    RT_ENV_DEV: 'http://dev.local:8000',
    RT_ENV_TEST: '',
    RT_ENV_PROD: 'http://prod.local:8000',
  })

  // 生成的文件通过 node --check（与 generate-runtime-config.sh 末尾同款校验）
  const check = spawnSync(process.execPath, ['--check', file], { encoding: 'utf-8' })
  assert.equal(check.status, 0, `node --check 失败: ${check.stderr}`)

  // 引号 / 反斜杠 / 换行 / CR 都以转义序列出现，而不是原样落进字符串字面量
  assert.ok(text.includes(`${RUNTIME_CONFIG_MARKER} = {`))
  assert.ok(text.includes('\\"'), '双引号应被转义')
  assert.ok(text.includes('\\\\'), '反斜杠应被转义')
  assert.ok(text.includes('\\n'), '换行应被转义')
  assert.ok(text.includes('\\r'), 'CR 应被转义')
  assert.ok(text.endsWith('};\n'))

  const cfg = parseRuntimeConfig(fs.readFileSync(file, 'utf-8'))
  assert.deepEqual(cfg, {
    AGENT_ENV: 'prod',
    API_BASE_URL: HOSTILE_API,
    APP_TITLE: HOSTILE_TITLE,
    APP_WELCOME_TITLE: welcome,
    ENV_CONFIG: {
      dev: { API_BASE_URL: 'http://dev.local:8000' },
      test: { API_BASE_URL: '' },
      prod: { API_BASE_URL: 'http://prod.local:8000' },
    },
  })
})

test('生成文件可被 node 正常执行，值里的代码注入只是字符串内容', (t) => {
  const dir = makeTempDir(t)
  const file = path.join(dir, 'runtime-config.js')
  const evil = '"; globalThis.__INJECTED__ = true; //'
  writeRuntimeConfig(file, { RT_API_BASE_URL: evil, RT_APP_TITLE: evil, RT_ENV_DEV: evil })

  // 用 node 真正加载生成文件：若转义有误，require 会报语法错误或执行注入语句
  const run = spawnSync(
    process.execPath,
    [
      '-e',
      `globalThis.window = {}; require(${JSON.stringify(file)});` +
        'process.stdout.write(JSON.stringify({' +
        ' cfg: globalThis.window.__RUNTIME_CONFIG__,' +
        ' injected: globalThis.__INJECTED__ === true }))',
    ],
    { encoding: 'utf-8' }
  )
  assert.equal(run.status, 0, `执行生成文件失败: ${run.stderr}`)
  const loaded = JSON.parse(run.stdout)
  assert.equal(loaded.injected, false, '生成文件不应执行值里的代码')
  assert.equal(loaded.cfg.API_BASE_URL, evil)
  assert.equal(loaded.cfg.APP_TITLE, evil)
  assert.equal(loaded.cfg.ENV_CONFIG.dev.API_BASE_URL, evil)
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

test('环境变量缺省时输出空串，键 / 结构与历史一致', () => {
  assert.deepEqual(configFromEnv({}), {
    AGENT_ENV: '',
    API_BASE_URL: '',
    APP_TITLE: '',
    APP_WELCOME_TITLE: '',
    ENV_CONFIG: {
      dev: { API_BASE_URL: '' },
      test: { API_BASE_URL: '' },
      prod: { API_BASE_URL: '' },
    },
  })
})
