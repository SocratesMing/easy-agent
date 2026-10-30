/**
 * 上传技能包校验的用例：格式（必须 zip）与名称规范（小写字母/数字/单个连字符）。
 *
 * 运行：`npm run test:unit`（node --test，不依赖 eslint）。
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_SKILL_NAME_CHARS,
  SKILL_NAME_RE,
  skillNameFromZip,
  validateSkillZip,
} from './skillZip.js'

const file = (name) => ({ name })

test('合法 zip 名称通过', () => {
  for (const name of ['pdf-convert.zip', 'docx.zip', 'skill2.zip', 'a1-b2-c3.zip']) {
    assert.equal(validateSkillZip(file(name)), null, `${name} 应通过`)
  }
})

test('扩展名大小写不敏感，但名称仍要小写', () => {
  assert.equal(validateSkillZip(file('pdf-convert.ZIP')), null)
  assert.match(validateSkillZip(file('PDF-Convert.zip')), /只能用/)
})

test('非 zip 一律拒绝', () => {
  for (const name of ['skill.tar.gz', 'skill.rar', 'skill', 'skill.zip.txt', 'skill.7z']) {
    const error = validateSkillZip(file(name))
    assert.ok(error && error.includes('.zip'), `${name} 应被拒绝`)
  }
})

test('名称只允许小写字母、数字与单个连字符', () => {
  const rejected = [
    '中文技能.zip',
    'PDF转换.zip',
    'pdf_convert.zip',
    'pdf convert.zip',
    'pdf--convert.zip',
    '-pdf-convert.zip',
    'pdf-convert-.zip',
    'pdf.convert.zip',
    'PDF-Convert.zip',
  ]
  for (const name of rejected) {
    assert.match(validateSkillZip(file(name)), /只能用/, `${name} 应被拒绝`)
  }
})

test('Windows 路径前缀只取最后一段', () => {
  assert.equal(skillNameFromZip('C:\\fakepath\\pdf-convert.zip'), 'pdf-convert')
  assert.equal(validateSkillZip(file('C:\\fakepath\\pdf-convert.zip')), null)
  assert.match(validateSkillZip(file('C:\\fakepath\\中文技能.zip')), /只能用/)
})

test('空文件与空名称有明确提示', () => {
  assert.match(validateSkillZip(null), /请选择/)
  assert.match(validateSkillZip(file('')), /请选择/)
  assert.match(validateSkillZip(file('.zip')), /不能为空/)
})

test('超长名称被拒绝', () => {
  const ok = `${'a'.repeat(MAX_SKILL_NAME_CHARS)}.zip`
  assert.equal(validateSkillZip(file(ok)), null)
  const tooLong = `${'a'.repeat(MAX_SKILL_NAME_CHARS + 1)}.zip`
  assert.match(validateSkillZip(file(tooLong)), /不能超过/)
})

test('正则与官方规范一致：不许首尾/连续连字符', () => {
  assert.equal(SKILL_NAME_RE.test('pdf-convert'), true)
  assert.equal(SKILL_NAME_RE.test('-pdf'), false)
  assert.equal(SKILL_NAME_RE.test('pdf-'), false)
  assert.equal(SKILL_NAME_RE.test('pdf--convert'), false)
  assert.equal(SKILL_NAME_RE.test(''), false)
})
