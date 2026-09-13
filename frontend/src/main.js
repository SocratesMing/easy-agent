import { createApp } from 'vue'
import './style.css'
import 'highlight.js/styles/github-dark.css'
import App from './App.vue'
import { loadStoredThemeColors } from './theme.js'

// 挂载前应用持久化的颜色令牌（无自定义时即浅色默认值）
loadStoredThemeColors()

createApp(App).mount('#app')
