import test from 'node:test'
import assert from 'node:assert/strict'
import { enhancementSnapshot } from '../shared/promptEnhancement/controller.js'
import * as tokens from './skillPillTokens.js'

test('enhancement preserves paths, indentation and ordered directory identities', () => {
  const map = tokens.buildTokenMap([{ name: '同名', dir_name: 'one' }, { name: '同名', dir_name: 'two' }])
  const text = '／two　分析：\n    /tmp/test\n／one　  code()\n／two　'
  const snapshot = enhancementSnapshot(text, map, tokens)
  assert.equal(snapshot.body, '分析：\n    /tmp/test\n  code()')
  assert.deepEqual(snapshot.tokens.map(t => t.dirName), ['two', 'one'])
  const restored = snapshot.restore('输出\n    内容')
  assert.deepEqual(tokens.skillDirsInText(restored, map), ['two', 'one'])
  assert.equal(tokens.stripSkillTokens(restored, map), '输出\n    内容')
  assert.equal(snapshot.restore('／one　／two　模型正文'), '／two　／one　模型正文')
  assert.throws(() => snapshot.restore('／one　'), /增强失败/)
  assert.equal(enhancementSnapshot('／one　', map, tokens).body, '')
  // Tokens typed without their trailing space must remain recognized on refill.
  assert.deepEqual(tokens.skillDirsInText(enhancementSnapshot('正文 ／one', map, tokens).restore('新正文'), map), ['one'])
})
