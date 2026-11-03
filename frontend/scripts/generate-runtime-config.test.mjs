import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { parseRuntimeConfig } from './runtime-config.mjs'

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url))
const SCRIPT_MJS = path.join(SCRIPT_DIR, 'generate-runtime-config.mjs')
const SCRIPT_SH = path.join(SCRIPT_DIR, 'generate-runtime-config.sh')

// npm run build / serve 走 node 入口（跨平台，不依赖 bash）；sh 包装脚本只在有 sh 的
// 环境跑集成用例（纯 Windows 环境跳过）。
const SH = (() => {
  const res = spawnSync('sh', ['-c', 'command -v sh'], { encoding: 'utf-8' })
  return res.status === 0 ? res.stdout.trim() : null
})()
const SH_SKIP = SH ? false : '需要 sh 才能测试 generate-runtime-config.sh'

// 每个用例单独建临时目录，结束后删除，不往仓库里留产物
function makeTempDir(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'easy-agent-generate-runtime-config-'))
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }))
  return dir
}

// 只透传 PATH / HOME 等运行必需变量，避免宿主机的 AGENT_ENV / API_BASE_URL / APP_TITLE 干扰断言
function baseEnv(nodeBinDir, extra = {}) {
  return {
    PATH: nodeBinDir ? `${nodeBinDir}${path.delimiter}${process.env.PATH || '/usr/local/bin:/usr/bin:/bin'}` : (process.env.PATH || '/usr/local/bin:/usr/bin:/bin'),
    HOME: process.env.HOME || os.tmpdir(),
    ...extra,
  }
}

const NODE_BIN = path.dirname(process.execPath)

function runNode(cwd, extraEnv = {}) {
  return spawnSync(process.execPath, [SCRIPT_MJS], { cwd, env: baseEnv(NODE_BIN, extraEnv), encoding: 'utf-8' })
}

function runSh(cwd, extraEnv = {}) {
  return spawnSync(SH, [SCRIPT_SH], { cwd, env: baseEnv(NODE_BIN, extraEnv), encoding: 'utf-8' })
}

/** 写 CRLF 行尾的 .env.<mode>，与仓库里的 .env.* 保持一致（生成器要先去掉 \r） */
function writeEnvFile(dir, mode, apiUrl, eol = '\r\n') {
  const lines = ['# 环境配置', `VITE_API_BASE_URL=${apiUrl}`]
  fs.writeFileSync(path.join(dir, `.env.${mode}`), lines.join(eol) + eol, 'utf-8')
}

function readGenerated(file) {
  return parseRuntimeConfig(fs.readFileSync(file, 'utf-8'))
}

test('未设置 AGENT_ENV 时回退同源 "/"，ENV_CONFIG 读取各 .env 且去掉 CR', (t) => {
  const dir = makeTempDir(t)
  writeEnvFile(dir, 'dev', 'http://dev.local:8000')
  writeEnvFile(dir, 'test', 'http://test.local:9007')
  writeEnvFile(dir, 'prod', 'http://prod.local:8000')
  const title = 'A "B" \\ C\n第二行\r尾'
  const res = runNode(dir, { APP_TITLE: title })

  assert.equal(res.status, 0, `生成失败: ${res.stderr}`)
  const cfg = readGenerated(path.join(dir, 'dist', 'runtime-config.js'))
  assert.equal(cfg.AGENT_ENV, 'prod')
  assert.equal(cfg.API_BASE_URL, '/')
  assert.equal(cfg.APP_TITLE, title) // hostile 值精确还原
  assert.equal(cfg.APP_WELCOME_TITLE, '')
  assert.deepEqual(cfg.ENV_CONFIG, {
    dev: { API_BASE_URL: 'http://dev.local:8000' },
    test: { API_BASE_URL: 'http://test.local:9007' },
    prod: { API_BASE_URL: 'http://prod.local:8000' },
  })
  assert.ok(res.stdout.includes('已生成运行期前端配置'))
  assert.ok(res.stdout.includes('相对路径 /'))
})

test('显式 API_BASE_URL 优先；显式 AGENT_ENV 才套用 .env.<AGENT_ENV> 地址', (t) => {
  const dir = makeTempDir(t)
  writeEnvFile(dir, 'dev', 'http://dev.local:8000')
  writeEnvFile(dir, 'test', 'http://test.local:9007')
  writeEnvFile(dir, 'prod', 'http://prod.local:8000')

  const explicit = 'http://explicit.local:9001/base'
  const res1 = runNode(dir, { AGENT_ENV: 'test', API_BASE_URL: explicit, DIST_DIR: 'out1' })
  assert.equal(res1.status, 0, `生成失败: ${res1.stderr}`)
  const cfg1 = readGenerated(path.join(dir, 'out1', 'runtime-config.js'))
  assert.equal(cfg1.AGENT_ENV, 'test')
  assert.equal(cfg1.API_BASE_URL, explicit)

  const res2 = runNode(dir, { AGENT_ENV: 'test', DIST_DIR: 'out2' })
  assert.equal(res2.status, 0, `生成失败: ${res2.stderr}`)
  const cfg2 = readGenerated(path.join(dir, 'out2', 'runtime-config.js'))
  assert.equal(cfg2.AGENT_ENV, 'test')
  assert.equal(cfg2.API_BASE_URL, 'http://test.local:9007')
  assert.ok(res2.stdout.includes('.env.test 的 VITE_API_BASE_URL'))
})

test('含引号 / 反斜杠 / 换行的 API_BASE_URL 仍生成合法 JS 且值精确', (t) => {
  const dir = makeTempDir(t)
  const api = 'http://127.0.0.1:9000/pre"fix\\x\nnext\rline'
  const outDir = path.join(dir, 'runtime-out')
  const res = runNode(dir, { API_BASE_URL: api, DIST_DIR: outDir })
  assert.equal(res.status, 0, `生成失败: ${res.stderr}`)

  const file = path.join(outDir, 'runtime-config.js')
  const check = spawnSync(process.execPath, ['--check', file], { encoding: 'utf-8' })
  assert.equal(check.status, 0, `node --check 失败: ${check.stderr}`)
  assert.equal(readGenerated(file).API_BASE_URL, api)
})

test('sh 包装脚本与 node 入口结果一致，并在缺少 node 时报错退出非 0', { skip: SH_SKIP }, (t) => {
  const dir = makeTempDir(t)
  writeEnvFile(dir, 'dev', 'http://dev.local:8000')
  const title = 'A "B" \\ C'

  const viaNode = runNode(dir, { APP_TITLE: title, DIST_DIR: 'node-out' })
  const viaSh = runSh(dir, { APP_TITLE: title, DIST_DIR: 'sh-out' })
  assert.equal(viaNode.status, 0, `node 入口失败: ${viaNode.stderr}`)
  assert.equal(viaSh.status, 0, `sh 包装失败: ${viaSh.stderr}`)
  assert.equal(
    fs.readFileSync(path.join(dir, 'sh-out', 'runtime-config.js'), 'utf-8'),
    fs.readFileSync(path.join(dir, 'node-out', 'runtime-config.js'), 'utf-8')
  )

  // PATH 置空（只保留目录本身）后 sh 包装应报“未找到 node”，且不产出配置文件
  const emptyBin = path.join(dir, 'empty-bin')
  fs.mkdirSync(emptyBin)
  const noNode = runSh(dir, { PATH: emptyBin, DIST_DIR: 'no-node' })
  assert.notEqual(noNode.status, 0)
  assert.match(noNode.stderr, /未找到 node/)
  assert.equal(fs.existsSync(path.join(dir, 'no-node', 'runtime-config.js')), false)
})
