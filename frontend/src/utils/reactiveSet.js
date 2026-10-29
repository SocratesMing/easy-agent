/**
 * 与 frontend-vue2/src/utils/reactiveSet.js 同签名：Vue 3 基于 Proxy，下标赋值与新增属性
 * 本身就是响应式的，这里只保留越界保护，便于两套基线共用同一份流式处理代码。
 *
 * @param {Array|Object} target 目标容器
 * @param {number|string} key 下标或属性名
 * @param {*} value 新值
 */
export function setReactive(target, key, value) {
  if (!target) return
  if (Array.isArray(target)) {
    if (key >= 0 && key < target.length) target[key] = value
  } else {
    target[key] = value
  }
}
