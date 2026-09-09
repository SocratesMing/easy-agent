import Vue from 'vue'
import ElementUI from 'element-ui'
// 顺序很重要：Tailwind 的 preflight 会把 button 背景重置为 transparent，
// 必须先加载 Tailwind、再加载 element-ui 主题，否则 el-button 会失去底色。
import './style.css'
import 'element-ui/lib/theme-chalk/index.css'
import 'highlight.js/styles/github-dark.css'
import App from './App.vue'

Vue.use(ElementUI)
Vue.config.productionTip = false

new Vue({
  render: (h) => h(App),
}).$mount('#app')
