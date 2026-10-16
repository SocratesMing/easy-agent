import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSegments,
  buildTokenMap,
  insertSkillToken,
  migrateLegacySkillTokens,
  reconcileTokenDeletion,
  removeTokenSegments,
  skillDirsInText,
  stripSkillTokens,
  tokenAfterCaret,
  tokenBeforeCaret,
  tokenForSkill,
} from './skillPillTokens.js'

const SKILLS = [
  { dir_name: 'docx', name: 'Word 文档处理', icon: '📄', category_id: 3 },
  { dir_name: 'pptx-generator', name: 'PPT 生成' },
]

const map = () => buildTokenMap(SKILLS)
const text = (segs) => segs.map((s) => (s.type === 'skill' ? s.token : s.text)).join('')
const T_WORD = '／Word 文档处理　'
const T_PPT = '／PPT 生成　'

test('标签文本优先显示名，首字符是全角斜线、末尾带一个全角空格（芯片右内边距 + 与后文的间隙）', () => {
  assert.equal(tokenForSkill({ dir_name: 'docx', name: 'Word 文档处理' }), T_WORD)
  assert.equal(tokenForSkill({ dir_name: 'word-docx' }), '／word-docx　')
  assert.ok(T_WORD.endsWith('　'), '尾随空格是芯片右内边距，不能省')
})

test('文本可原样还原（不丢字、不粘字）', () => {
  const cases = [
    '',
    '帮我用 ／Word 文档处理　排个版',
    '／PPT 生成　／Word 文档处理　一起做',
    '／PPT 生成／Word 文档处理　一起做',       // 紧邻（无尾随空格的旧形式）也要能还原
    '／docx 这是未知目录名，应当保持普通文字',
    '路径 /usr/bin/env 不是技能',
    '／Word 文档处理第二行\n换行也正常',
  ]
  for (const value of cases) {
    assert.equal(text(buildSegments(value, map())), value)
  }
})

test('分段携带图标与分类（镜像层渲染「图标 + 名字」用）', () => {
  const segs = buildSegments(T_WORD, map())
  assert.equal(segs.length, 1)
  assert.deepEqual(
    { label: segs[0].label, icon: segs[0].icon, categoryId: segs[0].categoryId, dirName: segs[0].dirName },
    { label: 'Word 文档处理　', icon: '📄', categoryId: 3, dirName: 'docx' }
  )
  assert.equal(segs[0].start, 0)
  assert.equal(segs[0].end, T_WORD.length)
})

test('没有尾随空格的旧形式（标签后直接接文字）也能命中，label 不含空格', () => {
  const segs = buildSegments('／Word 文档处理你好', map())
  assert.equal(segs.length, 2)
  assert.equal(segs[0].dirName, 'docx')
  assert.equal(segs[0].label, 'Word 文档处理')
  assert.equal(segs[0].end, '／Word 文档处理'.length)
  assert.equal(text(segs), '／Word 文档处理你好')
})

test('识别开头、空白后、以及紧邻上一个标签的 token', () => {
  const segs = buildSegments(T_PPT + T_WORD + '你好', map())
  const skills = segs.filter((s) => s.type === 'skill')
  assert.deepEqual(skills.map((s) => s.dirName), ['pptx-generator', 'docx'])
  assert.deepEqual(skills.map((s) => s.start), [0, T_PPT.length])
  assert.equal(text(segs), T_PPT + T_WORD + '你好')
})

test('同时命中的 token 取最长，位置判断不误伤相邻文字', () => {
  const tokens = buildTokenMap([{ dir_name: 'a', name: 'doc' }, { dir_name: 'b', name: 'docx' }])
  const segs = buildSegments('／docx　', tokens)
  assert.equal(segs.length, 1)
  assert.equal(segs[0].dirName, 'b')
  const other = buildSegments('／docs', tokens)
  assert.deepEqual(other.map((s) => s.type), ['skill', 'text'])
  assert.equal(text(other), '／docs')
})

test('同显示名的技能双方都回退目录名', () => {
  const tokens = buildTokenMap([
    { dir_name: 'one', name: '同名技能' },
    { dir_name: 'two', name: '同名技能' },
  ])
  assert.equal(tokens.has('／同名技能　'), false)
  assert.equal(tokens.get('／one　').dir_name, 'one')
  assert.equal(tokens.get('／two　').dir_name, 'two')
})

test('suppressStart 让正在检索的半截查询词保持普通文字', () => {
  const segs = buildSegments('／doc', buildTokenMap([{ dir_name: 'a', name: 'doc' }]), { suppressStart: 0 })
  assert.deepEqual(segs, [{ type: 'text', text: '／doc' }])
})

test('光标紧邻标签之后 / 之前才命中', () => {
  const value = '看下 ／Word 文档处理　的内容'
  const tokens = map()
  const start = value.indexOf('／Word 文档处理')
  const end = start + '／Word 文档处理　'.length
  assert.equal(tokenBeforeCaret(value, end, tokens).dirName, 'docx')
  assert.equal(tokenBeforeCaret(value, end - 1, tokens), null)
  assert.equal(tokenBeforeCaret(value, end + 1, tokens), null)
  assert.equal(tokenAfterCaret(value, start, tokens).dirName, 'docx')
  assert.equal(tokenAfterCaret(value, start + 1, tokens), null)
})

test('插入标签：前方紧贴文字时补空格，光标停在尾随空格之后（芯片外）', () => {
  const inserted = insertSkillToken('帮我做', 3, T_WORD)
  assert.equal(inserted.text, '帮我做 ' + T_WORD)
  assert.equal(inserted.caret, inserted.text.length)
  assert.equal(inserted.text[inserted.caret - 1], '　', '光标落在芯片右内边距之外')
})

test('插入标签：前方已是空白/开头时不重复补空格', () => {
  const atStart = insertSkillToken('', 0, T_WORD)
  assert.equal(atStart.text, T_WORD)
  assert.equal(atStart.caret, T_WORD.length)
  const afterSpace = insertSkillToken('你好 ', 3, T_WORD)
  assert.equal(afterSpace.text, '你好 ' + T_WORD)
})

test('插入标签：后面紧跟空格时吃掉一个，不留双空格', () => {
  const mid = insertSkillToken('ab cd', 2, T_WORD)
  assert.equal(mid.text, 'ab ' + T_WORD + 'cd')
  assert.ok(!/\s{2}/.test(mid.text), '不留双空格')
})

test('删除标签（含同一技能出现多次）后光标落在最前那个标签的起点', () => {
  const value = '／Word 文档处理　中间 ／Word 文档处理'
  const segs = buildSegments(value, map()).filter((s) => s.type === 'skill')
  const removed = removeTokenSegments(value, segs)
  assert.equal(removed.text, '中间 ')
  assert.equal(removed.caret, 0)
})

test('发送前剥离标签，技能目录名单独取出', () => {
  const value = T_WORD + T_PPT + '帮我生成'
  assert.equal(skillDirsInText(value, map()).join(','), 'docx,pptx-generator')
  assert.equal(stripSkillTokens(value, map()), '帮我生成')
})

test('移动端：被删掉半个标签时整段删掉标签', () => {
  const value = '前面 ／Word 文档处理　后面'
  const tokens = map()
  const end = value.indexOf('／Word 文档处理') + '／Word 文档处理　'.length
  // 浏览器只删掉了标签的最后一个字（光标停在 end-1）
  const afterNative = value.slice(0, end - 1) + value.slice(end)
  const healed = reconcileTokenDeletion(value, afterNative, end - 1, tokens)
  assert.equal(healed.text, '前面 后面')
  assert.equal(healed.caret, value.indexOf('／Word 文档处理'))
})

test('移动端：光标在尾随空格处 Backspace 也会整段删掉标签', () => {
  const value = '前面 ／Word 文档处理　后面'
  const tokens = map()
  const start = value.indexOf('／Word 文档处理')
  const end = start + '／Word 文档处理　'.length          // 含尾随空格
  const afterNative = value.slice(0, end - 1) + value.slice(end)
  const healed = reconcileTokenDeletion(value, afterNative, end - 1, tokens)
  assert.equal(healed.text, '前面 后面')
  assert.equal(healed.caret, start)
})

test('移动端兜底不误伤普通删除 / 粘贴 / 替换', () => {
  const value = '前面 ／Word 文档处理　后面'
  const tokens = map()
  // 普通文字的单字删除
  assert.equal(reconcileTokenDeletion(value, value.slice(0, 1) + value.slice(2), 1, tokens), null)
  // 删除点之前的文本被改动（粘贴 / 替换 / 输入法上屏）→ 交回常规处理
  assert.equal(reconcileTokenDeletion(value, 'X' + value.slice(1), 1, tokens), null)
  // 文本变长（输入）→ 不处理
  assert.equal(reconcileTokenDeletion(value, value + 'x', value.length, tokens), null)
  // 选区删除（如 Ctrl+A 全选）：用户删掉的内容不能被自愈找回
  assert.equal(reconcileTokenDeletion(value, '', 0, tokens), null)
  // 跨标签的大段选区删除 → 交回常规处理
  const bigDelete = value.slice(0, 2) + value.slice(20)
  assert.equal(reconcileTokenDeletion(value, bigDelete, 2, tokens), null)
})

test('移动端兜底不管「光标落在标签中间」的常规删除', () => {
  const value = '前面 ／Word 文档处理　后面'
  const tokens = map()
  const start = value.indexOf('／Word 文档处理')
  const caret = start + 5          // 标签内部
  const afterNative = value.slice(0, caret - 1) + value.slice(caret)
  assert.equal(reconcileTokenDeletion(value, afterNative, caret - 1, tokens), null)
})

test('旧版草稿迁移：半角 /、全角无尾空格、现行形式统一改写成现行形式', () => {
  const tokens = map()
  // ① 半角旧形式 → 现行形式（尾随空格），且不留双空格
  assert.equal(migrateLegacySkillTokens('/Word 文档处理 帮我排版', tokens), T_WORD + '帮我排版')
  // ② 全角但缺尾随空格 → 补上
  assert.equal(migrateLegacySkillTokens('／Word 文档处理 中间', tokens), T_WORD + '中间')
  // ③ 现行形式 → 原样（不动）
  assert.equal(migrateLegacySkillTokens(T_WORD + '中间', tokens), T_WORD + '中间')
  // 混排：只改旧的那段
  assert.equal(migrateLegacySkillTokens('/Word 文档处理 中间 ／PPT 生成 后面', tokens), T_WORD + '中间 ' + T_PPT + '后面')
  // 普通文字里的 /xxx 不是旧标签，保持原样
  assert.equal(migrateLegacySkillTokens('路径 /usr/bin/env 与 /docx', tokens), '路径 /usr/bin/env 与 /docx')
  // 空文本 / 空映射 → 原样返回
  assert.equal(migrateLegacySkillTokens('', tokens), '')
  assert.equal(migrateLegacySkillTokens('/Word 文档处理', new Map()), '/Word 文档处理')
})
