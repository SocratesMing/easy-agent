import Vue from 'vue'
// 顺序很重要：独立应用的全局重置（global.css）会把 button 背景重置为 transparent，
// 必须先加载重置、再加载 element-ui 主题，否则 el-button 会失去底色。
// Element UI 组件由用到的业务组件局部注册（全仓只用到 el-button / el-dialog 与 MessageBox），
// 这里只引入对应主题样式；嵌入宿主时由宿主提供 Element 主题。
import './standalone/global.css'
import 'element-ui/lib/theme-chalk/base.css'
import 'element-ui/lib/theme-chalk/icon.css'
import 'element-ui/lib/theme-chalk/button.css'
import 'element-ui/lib/theme-chalk/dialog.css'
import 'element-ui/lib/theme-chalk/message-box.css'
import { configureStandalone } from './standalone/adapter.js'
import App from './App.vue'

Vue.config.productionTip = false

// 挂载前写入运行时配置（登录方式、标题、主题颜色等），见 standalone/adapter.js
configureStandalone()

new Vue({
  render: (h) => h(App),
}).$mount('#app')
