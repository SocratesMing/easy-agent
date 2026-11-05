<template>
  <!-- 被其他设备顶下线：明确提示，不自动重新登录（避免同一账号多处无感重登、无限互顶） -->
  <div class="auth-blocked-box auth-kicked">
    <h2>账号已在其他设备登录</h2>
    <p>您的账号已在其他设备或页面登录，当前页面已退出登录。</p>
    <p class="auth-kicked-tip">
      为避免同一账号在多处互相顶替，本页不会自动重新登录。
      <template v-if="signInEnabled">如确认由您本人继续在此使用，请点击「重新登录」，其他设备或页面上的登录将随之退出。</template>
      <template v-else>如需继续使用，请重新登录后再进入本页面。</template>
    </p>
    <p v-if="error" class="auth-kicked-error">{{ error }}</p>
    <button
      v-if="signInEnabled"
      type="button"
      class="auth-kicked-btn"
      :disabled="loading"
      @click="handleRelogin"
    >{{ loading ? '正在重新登录…' : '重新登录' }}</button>
  </div>
</template>

<script>
export default {
  name: 'AuthKickedNotice',
  props: {
    // 是否配置了适配器登录（auth.signIn）：没有时只提示，不提供「重新登录」按钮
    signInEnabled: { type: Boolean, default: false },
    // (options) => Promise<boolean>：以 { manual: true } 调用，表示用户明确要求重新登录
    relogin: { type: Function, required: true },
  },
  data() {
    return {
      loading: false,
      error: '',
    }
  },
  methods: {
    async handleRelogin() {
      if (this.loading) return
      this.loading = true
      this.error = ''
      try {
        const ok = await this.relogin({ manual: true })
        if (!ok) this.error = '重新登录失败：未获取到登录信息或后端校验未通过，请从门户系统重新进入本页面。'
      } catch (e) {
        this.error = '重新登录失败，请稍后重试。'
      } finally {
        // 成功时父组件已切换到主界面、本组件随之销毁
        if (!this._isDestroyed) this.loading = false
      }
    },
  },
}
</script>

<style scoped>
.auth-blocked-box {
  max-width: 480px;
  padding: 32px;
  text-align: center;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  color: var(--text-secondary, #64748b);
}

.auth-blocked-box h2 {
  margin: 0 0 12px;
  font-size: 18px;
  color: var(--text-primary, #1e293b);
}

.auth-blocked-box p {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
}

.auth-blocked-box .auth-kicked-tip {
  margin-top: 8px;
}

.auth-blocked-box .auth-kicked-error {
  margin-top: 12px;
  color: #dc2626;
}

.auth-kicked-btn {
  margin-top: 20px;
  padding: 8px 28px;
  font-size: 14px;
  color: #ffffff;
  background: var(--accent-color, #2563eb);
  border: none;
  border-radius: 8px;
  cursor: pointer;
}

.auth-kicked-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
