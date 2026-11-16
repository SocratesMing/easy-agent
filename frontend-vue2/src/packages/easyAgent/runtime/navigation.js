/**
 * 页面地址读写（适配合同 navigation）。
 *
 * 模块只读写自己约定的 query 键（session / panel，独立应用另有 username / user_id），
 * 且只做「替换」不新增历史记录：
 * - 嵌入宿主：由宿主适配器经宿主路由实现（见 createVueRouterNavigation），模块不碰 history；
 * - 独立应用：读 location.search，用 history.replaceState 改写，保留 path、hash 与 history.state。
 */
import { getRuntime } from './config.js'

function firstValue(value) {
  return Array.isArray(value) ? value[0] : value
}

function normalizeQuery(query) {
  const out = {}
  for (const key of Object.keys(query || {})) {
    const value = firstValue(query[key])
    if (value !== undefined && value !== null) out[key] = String(value)
  }
  return out
}

/** 把 patch（值为 null / undefined 的键删除，其余替换）应用到 query，返回新对象 */
export function applyQueryPatch(query, patch) {
  const next = { ...(query || {}) }
  for (const key of Object.keys(patch || {})) {
    if (patch[key] === null || patch[key] === undefined) delete next[key]
    else next[key] = String(patch[key])
  }
  return next
}

/** location + patch → 新的 path?query#hash（只改 patch 涉及的键） */
export function buildLocationWithQuery(location, patch) {
  const params = new URLSearchParams(location.search || '')
  for (const key of Object.keys(patch || {})) {
    if (patch[key] === null || patch[key] === undefined) params.delete(key)
    else params.set(key, String(patch[key]))
  }
  const query = params.toString()
  return `${location.pathname}${query ? `?${query}` : ''}${location.hash || ''}`
}

/** 读取当前页面 query（值统一为字符串，重复键取第一个） */
export function readQuery(vm) {
  const runtime = getRuntime()
  const nav = runtime.navigation
  if (typeof nav.getQuery === 'function') return normalizeQuery(nav.getQuery(vm))
  if (runtime.mode !== 'standalone' || typeof window === 'undefined') return {}
  const out = {}
  new URLSearchParams(window.location.search).forEach((value, key) => {
    if (!(key in out)) out[key] = value
  })
  return out
}

/** 删除 / 替换 query 中的键（不新增历史记录） */
export function replaceQuery(vm, patch) {
  const runtime = getRuntime()
  const nav = runtime.navigation
  if (typeof nav.replaceQuery === 'function') return nav.replaceQuery(vm, patch)
  if (runtime.mode !== 'standalone' || typeof window === 'undefined') return undefined
  const target = buildLocationWithQuery(window.location, patch)
  window.history.replaceState(window.history.state, '', target)
  return undefined
}

/**
 * vue-router 3 的 navigation 实现（宿主适配器可直接使用）。
 * 用组件实例上的 $route / $router，保留 path、hash、params 与无关 query；
 * 同地址导航（NavigationDuplicated）静默忽略。
 */
export function createVueRouterNavigation() {
  return {
    getQuery(vm) {
      return vm && vm.$route ? { ...vm.$route.query } : {}
    },
    replaceQuery(vm, patch) {
      if (!vm || !vm.$router || !vm.$route) return undefined
      const route = vm.$route
      const query = applyQueryPatch(route.query, patch)
      const pending = vm.$router.replace({ path: route.path, query, hash: route.hash })
      if (pending && typeof pending.catch === 'function') return pending.catch(() => {})
      return pending
    },
  }
}
