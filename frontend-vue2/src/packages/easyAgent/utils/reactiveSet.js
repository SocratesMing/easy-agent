import Vue from 'vue'

/**
 * Vue 2 的响应式写入：数组下标赋值与对象新增属性都要走 Vue.set/splice，
 * 否则流式内容不会逐步渲染（Vue 3 基于 Proxy 无此限制）。
 *
 * @param {Array|Object} target 目标容器
 * @param {number|string} key 下标或属性名
 * @param {*} value 新值
 */
export function setReactive(target, key, value) {
  if (!target) return
  if (Array.isArray(target)) {
    if (key >= 0 && key < target.length) target.splice(key, 1, value)
  } else {
    Vue.set(target, key, value)
  }
}
