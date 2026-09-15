// 共享的 marked 扩展注册：数学公式(KaTeX) + GitHub 风格 emoji 短代码(:smile: 等)
// 通过 initialized 标志保证全局 marked 实例上的扩展只注册一次，
// 避免 ChatMessage / FilePreview 等多个组件重复注册导致扩展冲突。
import { marked } from 'marked'
import katexExtension from 'marked-katex-extension'
import { markedEmoji } from 'marked-emoji'
import { nameToEmoji } from 'gemoji'
import 'katex/dist/katex.min.css'

let initialized = false

// 将模型可能“内联”写出的块级公式 $$...$$ 规整到独立行，
// 否则 marked-katex-extension 的行内规则要求 $$ 前后有空格、块级规则要求 $$ 位于行首，
// 模型把公式嵌在段落中间（如 `text$$x$$text`）时不会被识别，最终以纯文本显示。
// 处理前先保护代码块/行内代码，避免把代码里的 $ 误当公式。
export function normalizeMathDelimiters(content) {
  if (typeof content !== 'string') return content
  const protectedChunks = []
  const placeholder = (chunk) => {
    protectedChunks.push(chunk)
    return ' CODE' + (protectedChunks.length - 1) + ' '
  }
  let s = content
    .replace(/```[\s\S]*?```/g, placeholder)
    .replace(/`[^`\n]+`/g, placeholder)
  s = s.replace(/\$\$([\s\S]+?)\$\$/g, (_, inner) => '\n\n$$\n' + inner.trim() + '\n$$\n\n')
  s = s.replace(/ CODE(\d+) /g, (_, i) => protectedChunks[+i])
  return s
}

export function setupMarkedExtensions() {
  if (initialized) return
  initialized = true

  // 数学公式：$$...$$ 块级、 $...$ 行内（标准模式，避免把普通美元金额误判为公式）
  marked.use(
    katexExtension({
      throwOnError: false,
      nonStandard: false,
      strict: false,
      errorColor: '#cc0000',
    })
  )

  // GitHub 风格 emoji 短代码，例如 :smile: :rocket: :fire: :+1: :tada:
  // gemoji.nameToEmoji 提供 短代码 -> unicode 字符 的完整映射
  marked.use(
    markedEmoji({
      emojis: nameToEmoji,
      renderer(token) {
        return '<span class="github-emoji" role="img" aria-label=":' + token.name + ':">' + token.emoji + '</span>'
      },
    })
  )
}
