// 宿主公共请求：全局 axios 拦截器，401 时整站跳转登录页（Easy Agent 的请求不能触发它）
import axios from 'axios'

export function installHostRequest(router) {
  axios.interceptors.response.use(
    (response) => response.data,
    (error) => {
      if (error.response && error.response.status === 401) {
        window.__hostEvents.push('host-401-redirect')
        router.push('/login')
      }
      return Promise.reject(error)
    }
  )
}
