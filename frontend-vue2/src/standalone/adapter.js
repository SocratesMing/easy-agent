/**
 * 外网独立应用的适配器：把原 App.vue 里写死的环境开关、门户共享存储读取与默认行为
 * 收拢到这里，经 configureEasyAgent() 交给业务模块。内网宿主不使用本文件
 * （宿主有自己的 src/integrations/easyAgent.js，模板见 integration/host-template/）。
 *
 * 构建期环境变量（.env.<mode> 或命令行）：
 *   VUE_APP_LOGIN_PAGE_ENABLED=false     关闭登录 / 注册页，改走门户免密登录（system:share:*）
 *   VUE_APP_PASSWORDLESS_DEMO_FALLBACK=true
 *                                        门户共享存储缺失时用 demo / demoid 免密登录
 *                                        （仅本地演示；默认关闭，身份缺失时显示「未授权访问」）
 */
import { configureEasyAgent, readStoredThemeColors } from '../packages/easyAgent/index.js'

// 登录页开关：true 显示登录 / 注册页（另支持 URL 免密直登 ?username=&user_id=）；
// false 不显示，改走门户免密登录，失败 → 「未授权访问」提示。未配置默认开启。
const LOGIN_PAGE_ENABLED = process.env.VUE_APP_LOGIN_PAGE_ENABLED !== 'false'
const DEMO_FALLBACK = process.env.VUE_APP_PASSWORDLESS_DEMO_FALLBACK === 'true'

// ── 门户共享存储键（由外部系统写入 localStorage）──────────────────────────
// system:share:loginId        登录账号     → 免密登录接口的 username
// system:share:iamEmpLoginNo  IAM 员工号   → 免密登录接口的 user_id
// system:share:name           用户姓名     → 展示用户名
// system:share:bankName       所属机构/银行 → organization_id
const SHARE_KEY_LOGIN_ID = 'system:share:loginId'
const SHARE_KEY_EMP_NO = 'system:share:iamEmpLoginNo'
const SHARE_KEY_NAME = 'system:share:name'
const SHARE_KEY_BANK = 'system:share:bankName'

/** 安全读取 localStorage（隐私模式 / 禁用存储时返回空串） */
function readShareItem(key) {
  try {
    return localStorage.getItem(key) || ''
  } catch (e) {
    return ''
  }
}

/** 门户免密登录：读共享存储里的身份换 Agent token，展示资料直接取共享存储 */
async function portalSignIn({ passwordlessLogin }) {
  const shareLoginId = readShareItem(SHARE_KEY_LOGIN_ID)
  const shareEmpNo = readShareItem(SHARE_KEY_EMP_NO)
  let username = shareLoginId
  let userId = shareEmpNo
  if (!username || !userId) {
    if (!DEMO_FALLBACK) {
      console.warn('[登录] 门户共享存储缺少 loginId / iamEmpLoginNo，不进行免密登录')
      return false
    }
    username = username || 'demo'
    userId = userId || 'demoid'
  }
  await passwordlessLogin(username, userId)
  return {
    profile: {
      // 用姓名展示；姓名缺失时回落到登录账号，避免界面显示为空
      username: readShareItem(SHARE_KEY_NAME) || username,
      organization_id: readShareItem(SHARE_KEY_BANK),
      email: shareLoginId,
    },
  }
}

export function configureStandalone() {
  configureEasyAgent({
    mode: 'standalone',
    auth: {
      loginPage: LOGIN_PAGE_ENABLED,
      urlLogin: true,
      // 与原行为一致：只有登录页关闭时才走门户免密登录；401 不续期，直接回到登录页 / 未授权提示
      signIn: LOGIN_PAGE_ENABLED ? null : portalSignIn,
    },
    app: { setDocumentTitle: true },
    // 用户持久化的颜色覆盖（原 main.js 的 loadStoredThemeColors）
    theme: { colors: readStoredThemeColors() },
    features: { aiSidebar: true, exposeGlobal: true },
  })
}
