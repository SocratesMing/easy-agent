// unplugin-icons 在 vue.config.js 中使用 compiler: 'raw'，默认导出的是 SVG 字符串；
// Vue 2 无法把字符串当作组件使用，这里用函数式组件把原始 SVG 渲染成 <svg> 根节点
// （保留 sunya 模板里 <IconX /> 的用法与 CSS 选择器结构）。

import rawIconActivity from '~icons/lucide/activity'
import rawIconArrowLeft from '~icons/lucide/arrow-left'
import rawIconArrowUp from '~icons/lucide/arrow-up'
import rawIconArrowUpRight from '~icons/lucide/arrow-up-right'
import rawIconBookDashed from '~icons/lucide/book-dashed'
import rawIconBookOpen from '~icons/lucide/book-open'
import rawIconBuilding2 from '~icons/lucide/building-2'
import rawIconCheck from '~icons/lucide/check'
import rawIconChevronDown from '~icons/lucide/chevron-down'
import rawIconChevronRight from '~icons/lucide/chevron-right'
import rawIconCircleAlert from '~icons/lucide/circle-alert'
import rawIconCircleCheck from '~icons/lucide/circle-check'
import rawIconClock3 from '~icons/lucide/clock-3'
import rawIconDatabase from '~icons/lucide/database'
import rawIconDownload from '~icons/lucide/download'
import rawIconEye from '~icons/lucide/eye'
import rawIconFileClock from '~icons/lucide/file-clock'
import rawIconFileText from '~icons/lucide/file-text'
import rawIconFiles from '~icons/lucide/files'
import rawIconFolder from '~icons/lucide/folder'
import rawIconFolderPlus from '~icons/lucide/folder-plus'
import rawIconLayoutGrid from '~icons/lucide/layout-grid'
import rawIconLibrary from '~icons/lucide/library'
import rawIconList from '~icons/lucide/list'
import rawIconListFilter from '~icons/lucide/list-filter'
import rawIconPanelRightClose from '~icons/lucide/panel-right-close'
import rawIconPencil from '~icons/lucide/pencil'
import rawIconPlus from '~icons/lucide/plus'
import rawIconRefreshCw from '~icons/lucide/refresh-cw'
import rawIconSearch from '~icons/lucide/search'
import rawIconSettings2 from '~icons/lucide/settings-2'
import rawIconShieldCheck from '~icons/lucide/shield-check'
import rawIconSparkles from '~icons/lucide/sparkles'
import rawIconSquare from '~icons/lucide/square'
import rawIconTrash2 from '~icons/lucide/trash-2'
import rawIconTriangleAlert from '~icons/lucide/triangle-alert'
import rawIconUpload from '~icons/lucide/upload'
import rawIconUploadCloud from '~icons/lucide/upload-cloud'
import rawIconUserRound from '~icons/lucide/user-round'
import rawIconUserRoundCog from '~icons/lucide/user-round-cog'
import rawIconUsers from '~icons/lucide/users'
import rawIconX from '~icons/lucide/x'

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

export const IconActivity = icon(rawIconActivity)
export const IconArrowLeft = icon(rawIconArrowLeft)
export const IconArrowUp = icon(rawIconArrowUp)
export const IconArrowUpRight = icon(rawIconArrowUpRight)
export const IconBookDashed = icon(rawIconBookDashed)
export const IconBookOpen = icon(rawIconBookOpen)
export const IconBuilding2 = icon(rawIconBuilding2)
export const IconCheck = icon(rawIconCheck)
export const IconChevronDown = icon(rawIconChevronDown)
export const IconChevronRight = icon(rawIconChevronRight)
export const IconCircleAlert = icon(rawIconCircleAlert)
export const IconCircleCheck = icon(rawIconCircleCheck)
export const IconClock3 = icon(rawIconClock3)
export const IconDatabase = icon(rawIconDatabase)
export const IconDownload = icon(rawIconDownload)
export const IconEye = icon(rawIconEye)
export const IconFileClock = icon(rawIconFileClock)
export const IconFileText = icon(rawIconFileText)
export const IconFiles = icon(rawIconFiles)
export const IconFolder = icon(rawIconFolder)
export const IconFolderPlus = icon(rawIconFolderPlus)
export const IconLayoutGrid = icon(rawIconLayoutGrid)
export const IconLibrary = icon(rawIconLibrary)
export const IconList = icon(rawIconList)
export const IconListFilter = icon(rawIconListFilter)
export const IconPanelRightClose = icon(rawIconPanelRightClose)
export const IconPencil = icon(rawIconPencil)
export const IconPlus = icon(rawIconPlus)
export const IconRefreshCw = icon(rawIconRefreshCw)
export const IconSearch = icon(rawIconSearch)
export const IconSettings2 = icon(rawIconSettings2)
export const IconShieldCheck = icon(rawIconShieldCheck)
export const IconSparkles = icon(rawIconSparkles)
export const IconSquare = icon(rawIconSquare)
export const IconTrash2 = icon(rawIconTrash2)
export const IconTriangleAlert = icon(rawIconTriangleAlert)
export const IconUpload = icon(rawIconUpload)
export const IconUploadCloud = icon(rawIconUploadCloud)
export const IconUserRound = icon(rawIconUserRound)
export const IconUserRoundCog = icon(rawIconUserRoundCog)
export const IconUsers = icon(rawIconUsers)
export const IconX = icon(rawIconX)
