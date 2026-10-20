import Vue from 'vue'
// Element UI 按需引入：全仓只用到 el-button / el-dialog 与 MessageBox，
// 全量 import 会把整套组件库（约 500KB+）打进 vendors。
import Button from 'element-ui/lib/button'
import Dialog from 'element-ui/lib/dialog'
// 顺序很重要：全局重置（style.css）会把 button 背景重置为 transparent，
// 必须先加载重置、再加载 element-ui 主题，否则 el-button 会失去底色。
import './style.css'
import 'element-ui/lib/theme-chalk/base.css'
import 'element-ui/lib/theme-chalk/icon.css'
import 'element-ui/lib/theme-chalk/button.css'
import 'element-ui/lib/theme-chalk/dialog.css'
import 'element-ui/lib/theme-chalk/message-box.css'
import 'highlight.js/styles/github-dark.css'
import App from './App.vue'
import { loadStoredThemeColors } from './theme.js'
import tipDirective from './directives/tip.js'

Vue.use(Button)
Vue.use(Dialog)
Vue.config.productionTip = false

// 悬浮提示指令（单例浮层，hover/focus 触发，触摸设备点击切换）
Vue.directive('tip', tipDirective)

// 挂载前应用持久化的颜色令牌（无自定义时即浅色默认值）
loadStoredThemeColors()

new Vue({
  render: (h) => h(App),
}).$mount('#app')
