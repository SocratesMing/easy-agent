/**
 * Easy Agent 业务模块 · 稳定公开入口（宿主路由页面与外网独立应用都只从这里引用）。
 *
 * 用法（宿主）：
 *   import { configureEasyAgent, EasyAgentPage } from '@/packages/easyAgent'
 *   configureEasyAgent({ mode: 'embedded', api: {...}, auth: {...} })   // 渲染页面前调用
 *   // 路由组件里渲染 <EasyAgentPage />，父容器须有确定高度
 * 完整示例：integration/host-template/（src/integrations/easyAgent.js、src/pages/easyAgent/index.vue）。
 *
 * 本入口不做任何全局注册（无 Vue.use / Vue.directive / Vue.component），不改 document.title、
 * 不写 html / body；Element UI 主题 CSS 由宿主提供（模块只引入 element-ui 的组件 JS）。
 * 依赖清单见同目录 dependencies.json，模块约定见 README.md。
 */
import './styles/base.css'

export { default as EasyAgentPage } from './EasyAgentPage.vue'
export {
  EASY_AGENT_API_PREFIXES,
  EASY_AGENT_CONTRACT_VERSION,
  configureEasyAgent,
  getRuntime as getEasyAgentConfig,
} from './runtime/config.js'
export { createVueRouterNavigation } from './runtime/navigation.js'
// 被其他设备顶下线标记：宿主在自己的登录流程（如跳转内网登录页重新登录）完成后，
// 可调用 clearAuthKicked() 让模块恢复自动免密登录；不调用时由用户在页面上点击「重新登录」
export { clearAuthKicked, isAuthKicked } from './utils/request.js'
export { DEFAULT_COLORS, readStoredThemeColors, setThemeColors } from './runtime/theme.js'
