// 图标是模块自带的 SVG 字符串（assets/icons/lucide.js，由 scripts/easy-agent/generate-icons.mjs
// 生成，原为 `~icons/lucide/*` + 外网 webpack 插件）。Vue 2 无法把字符串当作组件使用，这里用
// 函数式组件把原始 SVG 渲染成 <svg> 根节点（保留 sunya 模板里 <IconX /> 的用法与 CSS 选择器结构）。
import * as lucide from '../../assets/icons/lucide.js'

function parseSvg(raw) {
  const match = /<svg([^>]*)>([\s\S]*)<\/svg>\s*$/.exec(String(raw).trim())
  if (!match) return { attrs: {}, inner: String(raw) }
  const attrs = {}
  const attrRe = /([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*"([^"]*)"/g
  let m
  while ((m = attrRe.exec(match[1])) !== null) attrs[m[1]] = m[2]
  return { attrs, inner: match[2] }
}

export function icon(raw) {
  const { attrs, inner } = parseSvg(raw)
  return {
    functional: true,
    render(h, context) {
      return h('svg', {
        ...context.data,
        attrs: { ...attrs, ...((context.data && context.data.attrs) || {}) },
        domProps: { innerHTML: inner },
      })
    },
  }
}

export const IconActivity = icon(lucide.activity)
export const IconArrowLeft = icon(lucide.arrowLeft)
export const IconArrowUp = icon(lucide.arrowUp)
export const IconArrowUpRight = icon(lucide.arrowUpRight)
export const IconBookDashed = icon(lucide.bookDashed)
export const IconBookOpen = icon(lucide.bookOpen)
export const IconBuilding2 = icon(lucide.building2)
export const IconCheck = icon(lucide.check)
export const IconChevronDown = icon(lucide.chevronDown)
export const IconChevronRight = icon(lucide.chevronRight)
export const IconCircleAlert = icon(lucide.circleAlert)
export const IconCircleCheck = icon(lucide.circleCheck)
export const IconClock3 = icon(lucide.clock3)
export const IconDatabase = icon(lucide.database)
export const IconDownload = icon(lucide.download)
export const IconEye = icon(lucide.eye)
export const IconFileClock = icon(lucide.fileClock)
export const IconFileText = icon(lucide.fileText)
export const IconFiles = icon(lucide.files)
export const IconFolder = icon(lucide.folder)
export const IconFolderPlus = icon(lucide.folderPlus)
export const IconPause = icon(lucide.pause)
export const IconPlay = icon(lucide.play)
export const IconLayoutGrid = icon(lucide.layoutGrid)
export const IconLibrary = icon(lucide.library)
export const IconList = icon(lucide.list)
export const IconListFilter = icon(lucide.listFilter)
export const IconMessageSquarePlus = icon(lucide.messageSquarePlus)
export const IconPanelRightClose = icon(lucide.panelRightClose)
export const IconPencil = icon(lucide.pencil)
export const IconPlus = icon(lucide.plus)
export const IconRefreshCw = icon(lucide.refreshCw)
export const IconSearch = icon(lucide.search)
export const IconSettings2 = icon(lucide.settings2)
export const IconShieldCheck = icon(lucide.shieldCheck)
export const IconSparkles = icon(lucide.sparkles)
export const IconSquare = icon(lucide.square)
export const IconTrash2 = icon(lucide.trash2)
export const IconTriangleAlert = icon(lucide.triangleAlert)
export const IconUpload = icon(lucide.upload)
export const IconUploadCloud = icon(lucide.uploadCloud)
export const IconUserRound = icon(lucide.userRound)
export const IconUserRoundCog = icon(lucide.userRoundCog)
export const IconUsers = icon(lucide.users)
export const IconX = icon(lucide.x)
