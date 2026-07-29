<template>
  <div class="welcome-overlay">
    <div class="welcome-modal">
      <div class="welcome-header">
        <div class="logo">
          <EasyLogo :size="36" />
        </div>
        <h1>欢迎使用 Easy Agent</h1>
        <p v-if="isResetPassword">重置密码</p>
        <p v-else>{{ isLogin ? '请登录您的账号' : '创建新账号开始使用' }}</p>
      </div>

      <form @submit.prevent="handleSubmit" class="welcome-form">
        <div class="form-group">
          <label for="username">
            用户名 <span class="required">*</span>
          </label>
          <input
            id="username"
            v-model="form.username"
            type="text"
            placeholder="用户名: admin"
            required
            ref="usernameInput"
          />
        </div>

        <div class="form-group" v-if="!isResetPassword">
          <label for="password">
            密码 <span class="required">*</span>
            <span class="password-hint">（4-20位，任意字符）</span>
          </label>
          <input
            id="password"
            v-model="form.password"
            type="password"
            placeholder="密码: 1234"
            required
          />
        </div>

        <div class="form-group" v-if="isResetPassword">
          <label for="newPassword">
            新密码 <span class="required">*</span>
            <span class="password-hint">（4-20位，任意字符）</span>
          </label>
          <input
            id="newPassword"
            v-model="form.newPassword"
            type="password"
            placeholder="请输入新密码"
            required
          />
        </div>

        <div class="form-group" v-if="!isLogin && !isResetPassword">
          <label for="organizationId">
            机构ID <span class="required">*</span>
            <span class="password-hint">（注册后不可更改）</span>
          </label>
          <input
            id="organizationId"
            v-model="form.organizationId"
            type="text"
            placeholder="请输入所属机构ID"
            required
          />
        </div>

        <div class="form-group" v-if="!isLogin && !isResetPassword">
          <label for="email">用户邮箱</label>
          <input
            id="email"
            v-model="form.email"
            type="email"
            placeholder="请输入用户邮箱（选填）"
          />
        </div>

        <div v-if="!isLogin && !isResetPassword" class="info-message">
          用户名将与当前 IP 地址绑定，仅可在该 IP 下登录与使用本账号
        </div>

        <div v-if="error" class="error-message">
          {{ error }}
        </div>

        <div v-if="success" class="success-message">
          {{ success }}
        </div>

        <button type="submit" class="submit-btn" :disabled="submitting || !form.username.trim() || (!isResetPassword && !form.password.trim()) || (!isLogin && !isResetPassword && !form.organizationId.trim())">
          {{ submitting ? (isResetPassword ? '重置中...' : (isLogin ? '登录中...' : '注册中...')) : (isResetPassword ? '重置密码' : (isLogin ? '登录' : '注册')) }}
        </button>

        <div class="form-footer">
          <template v-if="isResetPassword">
            <button type="button" @click="backToLogin" class="toggle-mode-btn">
              返回登录
            </button>
          </template>
          <template v-else>
            <button type="button" @click="toggleMode" class="toggle-mode-btn">
              {{ isLogin ? '还没有账号？立即注册' : '已有账号？立即登录' }}
            </button>
            <button v-if="isLogin" type="button" @click="goToResetPassword" class="forgot-password-btn">
              忘记密码？
            </button>
          </template>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { login, register, resetPassword } from '../api/auth.js'
import EasyLogo from './EasyLogo.vue'

const emit = defineEmits(['completed'])

const usernameInput = ref(null)
const submitting = ref(false)
const error = ref('')
const success = ref('')
const isLogin = ref(true)
const isResetPassword = ref(false)

const form = ref({
  username: '',
  password: '',
  organizationId: '',
  email: '',
  newPassword: ''
})

function toggleMode() {
  isLogin.value = !isLogin.value
  isResetPassword.value = false
  error.value = ''
  success.value = ''
  form.value = {
    username: '',
    password: '',
    organizationId: '',
    email: '',
    newPassword: ''
  }
}

function goToResetPassword() {
  isResetPassword.value = true
  error.value = ''
  success.value = ''
  form.value.password = ''
  form.value.newPassword = ''
}

function backToLogin() {
  isResetPassword.value = false
  error.value = ''
  success.value = ''
  form.value = {
    username: '',
    password: '',
    organizationId: '',
    email: '',
    newPassword: ''
  }
}

async function handleSubmit() {
  if (!form.value.username.trim()) {
    error.value = '请输入用户名'
    return
  }

  if (isResetPassword.value) {
    if (!form.value.newPassword.trim()) {
      error.value = '请输入新密码'
      return
    }
    if (form.value.newPassword.length < 4 || form.value.newPassword.length > 20) {
      error.value = '密码长度应为4-20位'
      return
    }
  } else {
    if (!form.value.password.trim()) {
      error.value = '请输入密码'
      return
    }

    if (form.value.password.length < 4 || form.value.password.length > 20) {
      error.value = '密码长度应为4-20位'
      return
    }
  }

  submitting.value = true
  error.value = ''
  success.value = ''

  try {
    if (isResetPassword.value) {
      await resetPassword(form.value.username.trim(), form.value.newPassword)
      success.value = '密码重置成功，请使用新密码登录'
      setTimeout(() => {
        backToLogin()
      }, 1500)
      return
    }

    let data
    if (isLogin.value) {
      data = await login(form.value.username.trim(), form.value.password)
    } else {
      if (!form.value.organizationId.trim()) {
        error.value = '请输入机构ID'
        submitting.value = false
        return
      }
      data = await register(
        form.value.username.trim(),
        form.value.password,
        form.value.organizationId.trim(),
        form.value.email.trim()
      )
    }

    emit('completed', {
      username: data.username,
      token: data.access_token,
      max_input_tokens: data.max_input_tokens
    })
  } catch (e) {
    if (e.status === 404) {
      error.value = '用户名不存在'
    } else if (e.status === 401) {
      error.value = '密码错误'
    } else {
      error.value = e.message || (isResetPassword.value ? '密码重置失败' : (isLogin.value ? '登录失败，请重试' : '注册失败，请重试'))
    }
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  nextTick(() => {
    usernameInput.value?.focus()
  })
})
</script>

<style scoped>
.welcome-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.welcome-modal {
  background: white;
  border-radius: 20px;
  width: 420px;
  max-width: 90vw;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
  overflow: hidden;
}

.welcome-header {
  text-align: center;
  padding: 40px 32px 24px;
}

.logo {
  display: flex;
  justify-content: center;
  margin-bottom: 20px;
}

.welcome-header h1 {
  margin: 0 0 8px 0;
  font-size: 24px;
  font-weight: 600;
  color: var(--text-primary);
}

.welcome-header p {
  margin: 0;
  font-size: 14px;
  color: var(--text-secondary);
}

.welcome-form {
  padding: 0 32px 32px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group label {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-secondary);
}

.required {
  color: #ef4444;
}

.password-hint {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-secondary);
  margin-left: 4px;
}

.form-group input {
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  font-size: 15px;
  background: var(--bg-tertiary);
  color: var(--text-primary);
  transition: all 0.2s;
  outline: none;
}

.form-group input:focus {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1);
}

.form-group input::placeholder {
  color: var(--text-secondary);
}

.error-message {
  padding: 12px 16px;
  background: #fee2e2;
  color: #dc2626;
  border-radius: 10px;
  font-size: 14px;
}

.info-message {
  padding: 12px 16px;
  background: rgba(14, 165, 233, 0.1);
  color: #0284c7;
  border: 1px solid rgba(14, 165, 233, 0.25);
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.5;
}

.success-message {
  padding: 12px 16px;
  background: #d1fae5;
  color: #059669;
  border-radius: 10px;
  font-size: 14px;
}

.submit-btn {
  padding: 14px 24px;
  border: none;
  background: linear-gradient(135deg, #0ea5e9 0%, #8b5cf6 100%);
  color: white;
  border-radius: 12px;
  font-size: 16px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  margin-top: 8px;
}

.submit-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3);
}

.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.form-footer {
  text-align: center;
  margin-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.toggle-mode-btn {
  background: transparent;
  border: none;
  color: #0ea5e9;
  font-size: 14px;
  cursor: pointer;
  padding: 8px 16px;
  transition: all 0.2s;
}

.toggle-mode-btn:hover {
  color: #0284c7;
  text-decoration: underline;
}

.forgot-password-btn {
  background: transparent;
  border: none;
  color: var(--text-secondary);
  font-size: 13px;
  cursor: pointer;
  padding: 4px 16px;
  transition: all 0.2s;
}

.forgot-password-btn:hover {
  color: var(--text-secondary);
  text-decoration: underline;
}
</style>
