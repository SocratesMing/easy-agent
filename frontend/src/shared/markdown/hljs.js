/**
 * highlight.js 精简实例：只注册常用语言。
 *
 * 全量 `import hljs from 'highlight.js'` 会带上约 190 种语言（近 1MB），
 * 而实际回复里的代码块集中在下面这些。未注册的语言会退回纯文本，
 * `highlightAuto` 也只在已注册集合里猜测。
 */

import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import c from 'highlight.js/lib/languages/c'
import cpp from 'highlight.js/lib/languages/cpp'
import csharp from 'highlight.js/lib/languages/csharp'
import css from 'highlight.js/lib/languages/css'
import diff from 'highlight.js/lib/languages/diff'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import go from 'highlight.js/lib/languages/go'
import ini from 'highlight.js/lib/languages/ini'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import rust from 'highlight.js/lib/languages/rust'
import scss from 'highlight.js/lib/languages/scss'
import shell from 'highlight.js/lib/languages/shell'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const LANGS = {
  bash, c, cpp, csharp, css, diff, dockerfile, go, ini, java, javascript,
  json, markdown, python, rust, scss, shell, sql, typescript, xml, yaml,
}

for (const [name, definition] of Object.entries(LANGS)) {
  hljs.registerLanguage(name, definition)
}

export default hljs
