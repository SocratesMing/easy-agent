// 代表性宿主入口：全量 Element UI、宿主全局样式、宿主 axios 默认值与全局 401 跳转、宿主自己的 v-tip 指令。
// Easy Agent 模块不依赖这里的任何初始化（没有 Vue.use / 全局注册 / alias 的隐式帮助）。
import Vue from 'vue'
import ElementUI from 'element-ui'
import 'element-ui/lib/theme-chalk/index.css'
import './styles/host.css'
import axios from 'axios'
import App from './App.vue'
import router from './router'
import { installHostRequest } from './api/request'

Vue.use(ElementUI)
Vue.config.productionTip = false

// 宿主同名指令：模块内的 v-tip 是局部注册，不能被这里覆盖，也不能覆盖这里
Vue.directive('tip', {
  bind(el, binding) {
    el.setAttribute('data-host-tip', binding.value || '')
  },
})

// 宿主改了全局 axios 默认值：模块自己的 axios 实例不能带上宿主 token / 前缀
axios.defaults.headers.common.Authorization = 'Bearer HOST-USER-TOKEN'
axios.defaults.baseURL = '/host-gateway'
installHostRequest(router)

// 宿主自己的暗色标记：模块样式只看模块根节点，不能因此变暗，也不能改宿主元素
document.documentElement.setAttribute('data-theme', 'dark')

// 门户共享登录信息（真实环境由门户写入），只有宿主适配器读取
localStorage.setItem('system:share:loginId', 'host_user')
localStorage.setItem('system:share:iamEmpLoginNo', 'E1001')
localStorage.setItem('system:share:name', '宿主用户')
localStorage.setItem('system:share:bankName', '示例分行')

window.__hostEvents = []
window.addEventListener('auth-expired', () => window.__hostEvents.push('auth-expired'))
window.addEventListener('user-activity', () => window.__hostEvents.push('user-activity'))

new Vue({
  router,
  render: (h) => h(App),
}).$mount('#app')
