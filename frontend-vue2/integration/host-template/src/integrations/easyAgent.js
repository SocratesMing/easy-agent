/**
 * Easy Agent 宿主适配器（首次接入时复制到宿主 src/integrations/easyAgent.js，之后归宿主维护；
 * 同步工具只更新 src/packages/easyAgent/，不会覆盖本文件）。
 *
 * 宿主与外网的所有差异都写在这里：后端地址、身份来源、登录失效提示、导航、标题、功能开关。
 * 业务模块（src/packages/easyAgent）不读 system:share:* 等宿主私有存储键，也不碰宿主登录态。
 * 字段说明：迁移包 integration/README.md「适配合同」。合同版本：1。
 */
import { configureEasyAgent, createVueRouterNavigation } from '@/packages/easyAgent'

// ── 宿主身份：门户写入 localStorage 的共享登录信息（键名以内网实际为准） ──────────────
//   loginId       登录账号 → 免密登录接口的 username
//   iamEmpLoginNo IAM 员工号 → 免密登录接口的 user_id
//   name / bankName 仅用于界面展示
const SHARE_KEYS = {
  loginId: 'system:share:loginId',
  empNo: 'system:share:iamEmpLoginNo',
  name: 'system:share:name',
  bank: 'system:share:bankName',
}

function readShare(key) {
  try {
    return localStorage.getItem(key) || ''
  } catch (e) {
    return ''
  }
}

configureEasyAgent({
  mode: 'embedded',
  api: {
    // Agent 后端地址前缀。同源（宿主 Nginx 把 /agent、/api/knowledge、/api/personnel 反代到 Agent 后端）时留空；
    // 跨域直连时填 Agent 后端地址（后端需允许该来源的 CORS）。需要按前缀分别改写时改用 resolveUrl(path)。
    baseURL: process.env.VUE_APP_EASY_AGENT_BASE_URL || '',
    // 公司网关要求的附加请求头（不要在这里放宿主自己的登录 token）
    // headers: () => ({ 'X-Request-Source': 'portal' }),
  },
  auth: {
    // Agent token 的存储键（与宿主自己的 token 分开存放；默认 mini_agent_token / mini_agent_username，
    // 与 AI 侧边栏 SDK 的 shareMainAppLogin 互通。不需要互通时可改成自己的键名）
    // tokenKey: 'mini_agent_token',
    // usernameKey: 'mini_agent_username',
    loginPage: false,
    // 用宿主身份换 Agent token（后端免密登录 /agent/auth/login-passwordless）。
    // 身份缺失时返回 false：模块显示「未授权访问」，不会回落到 demo 账号。
    // 401 时模块会自动重新执行本函数一次（并发合并）；被其他设备顶下线时不自动重登，避免两端互踢。
    async signIn({ passwordlessLogin }) {
      const loginId = readShare(SHARE_KEYS.loginId)
      const empNo = readShare(SHARE_KEYS.empNo)
      if (!loginId || !empNo) return false
      await passwordlessLogin(loginId, empNo)
      return {
        profile: {
          username: readShare(SHARE_KEYS.name) || loginId,
          organization_id: readShare(SHARE_KEYS.bank),
          email: loginId,
        },
      }
    },
    // 只表示 Agent 登录态失效（reason: expired | kicked），宿主登录不受影响：可以提示，不要在这里登出宿主
    onExpired({ reason }) {
      console.warn(`[EasyAgent] Agent 登录态失效：${reason}`)
    },
  },
  app: {
    title: 'Easy Agent',
    // 首页欢迎语（后端 /agent/auth/config 的 app_welcome_title 仍可覆盖）
    // welcomeTitle: 'Easy Agent，让工作化繁为简',
    setDocumentTitle: false,
  },
  // 经宿主 vue-router 读写 ?session= / ?panel=，保留 path、hash 与其他 query
  navigation: createVueRouterNavigation(),
  features: {
    aiSidebar: true,
    exposeGlobal: false,
  },
})
