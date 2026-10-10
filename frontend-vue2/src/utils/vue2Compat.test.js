import assert from 'node:assert/strict'
import test from 'node:test'
import { readdir, readFile } from 'node:fs/promises'
import path from 'node:path'

const frontendRoot = path.resolve(import.meta.dirname, '../..')

async function collectVueFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []

  for (const entry of entries) {
    const entryPath = path.join(directory, entry.name)
    if (entry.isDirectory()) {
      files.push(...await collectVueFiles(entryPath))
    } else if (entry.isFile() && entry.name.endsWith('.vue')) {
      files.push(entryPath)
    }
  }

  return files
}

test('frontend source uses Vue 2 compatible SFC syntax', async () => {
  const vueFiles = await collectVueFiles(path.join(frontendRoot, 'src'))
  assert.ok(vueFiles.length > 0, 'expected Vue SFCs to be present')

  const incompatibleSyntax = [
    '<script setup',
    'defineProps',
    'defineEmits',
    'defineExpose',
    '<Teleport',
    'v-model:',
  ]

  for (const filePath of vueFiles) {
    const source = await readFile(filePath, 'utf8')
    for (const syntax of incompatibleSyntax) {
      assert.equal(
        source.includes(syntax),
        false,
        `${path.relative(frontendRoot, filePath)} contains ${syntax}`
      )
    }
  }
})
