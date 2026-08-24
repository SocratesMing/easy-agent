import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { resolveEnvMode } from './env-mode.mjs'

const require = createRequire(import.meta.url)
const command = process.argv[2] || 'dev'
const mode = resolveEnvMode(command)
const cliPath = resolve(require.resolve('@vue/cli-service/bin/vue-cli-service.js'))
const args = command === 'build' ? ['build', '--mode', mode] : ['serve', '--mode', mode]

const result = spawnSync(process.execPath, [cliPath, ...args], {
  stdio: 'inherit',
  env: process.env,
})

process.exit(result.status ?? 0)
