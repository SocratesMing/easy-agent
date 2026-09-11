<template>
  <div class="welcome-overlay">
    <div class="welcome-modal">
      <div class="welcome-header">
        <h1>{{ APP_TITLE }}</h1>
        <p>{{ isLogin ? '请登录您的账号' : '创建新账号开始使用' }}</p>
      </div>

      <form @submit.prevent="handleSubmit" class="welcome-form">
        <div class="form-group">
          <label for="username">
            {{ isLogin ? '用户名 / 工号' : '用户名' }} <span class="required">*</span>
          </label>
          <input
            id="username"
            v-model="form.username"
            type="text"
            :placeholder="isLogin ? '请输入用户名或工号' : '用户名: admin'"
            required
            ref="usernameInput"
          />
        </div>

        <div class="form-group">
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

        <div class="form-group" v-if="!isLogin">
          <label for="employeeId">
            工号 <span class="required">*</span>
            <span class="password-hint">（全局唯一，注册后不可更改）</span>
          </label>
          <input
            id="employeeId"
            v-model="form.employeeId"
            type="text"
            placeholder="请输入工号"
            required
          />
        </div>

        <div v-if="!isLogin" class="info-message">
          账号支持单点登录：同账号新登录会自动踢掉此前的登录，登录不限制 IP
        </div>

        <div v-if="error" class="error-message">
          {{ error }}
        </div>

        <div v-if="success" class="success-message">
          {{ success }}
        </div>

        <button type="submit" class="submit-btn" :disabled="submitting || !form.username.trim() || !form.password.trim() || (!isLogin && !form.employeeId.trim())">
          {{ submitting ? (isLogin ? '登录中...' : '注册中...') : (isLogin ? '登录' : '注册') }}
        </button>

        <div class="form-footer">
          <button type="button" @click="toggleMode" class="toggle-mode-btn">
            {{ isLogin ? '还没有账号？立即注册' : '已有账号？立即登录' }}
          </button>
          <span v-if="isLogin" class="forgot-password-btn">忘记密码？请联系管理员 admin 重置</span>
        </div>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { login, register } from '../api/auth.js'
import { APP_TITLE } from '../config.js'

const emit = defineEmits(['completed'])

const usernameInput = ref(null)
const submitting = ref(false)
const error = ref('')
const success = ref('')
const isLogin = ref(true)

const form = ref({
  username: '',
  password: '',
  employeeId: ''
})

function toggleMode() {
  isLogin.value = !isLogin.value
  error.value = ''
  success.value = ''
  form.value = {
    username: '',
    password: '',
    employeeId: ''
  }
}

async function handleSubmit() {
  if (!form.value.username.trim()) {
    error.value = '请输入用户名'
    return
  }

  if (!form.value.password.trim()) {
    error.value = '请输入密码'
    return
  }

  if (!isLogin.value && (form.value.password.length < 4 || form.value.password.length > 20)) {
    error.value = '密码长度应为4-20位'
    return
  } else {
    if (form.value.password.length > 20) {
      error.value = '密码长度不能超过20位'
      return
    }
  }

  submitting.value = true
  error.value = ''
  success.value = ''

  try {
    let data
    if (isLogin.value) {
      data = await login(form.value.username.trim(), form.value.password)
    } else {
      if (!form.value.employeeId.trim()) {
        error.value = '请输入工号'
        submitting.value = false
        return
      }
      data = await register(
        form.value.username.trim(),
        form.value.password,
        form.value.employeeId.trim()
      )
    }

    emit('completed', {
      username: data.username,
      token: data.access_token,
      context_length: data.context_length
    })
  } catch (e) {
    if (e.status === 404) {
      error.value = '用户名或工号不存在'
    } else if (e.status === 401) {
      error.value = '用户名/工号或密码错误'
    } else {
      error.value = e.message || (isLogin.value ? '登录失败，请重试' : '注册失败，请重试')
    }
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  // 检测是否因单点登录被踢下线（账号在其他设备登录）
  if (localStorage.getItem('auth_kicked') === '1') {
    localStorage.removeItem('auth_kicked')
    error.value = '您的账号在其他设备登录，您已被迫下线，请重新登录'
  }
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
  background: #ffffff;
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
  border: 1px solid #e5e7eb;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.08);
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
  color: #1e293b;
}

.welcome-header p {
  margin: 0;
  font-size: 14px;
  color: #64748b;
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
  color: #64748b;
}

.required {
  color: #ef4444;
}

.password-hint {
  font-size: 12px;
  font-weight: 400;
  color: #94a3b8;
  margin-left: 4px;
}

.form-group input {
  padding: 14px 16px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  font-size: 15px;
  background: #f8fafc;
  color: #1e293b;
  transition: all 0.2s;
  outline: none;
}

.form-group input:focus {
  border-color: #0ea5e9;
  box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1);
}

.form-group input::placeholder {
  color: #94a3b8;
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
  color: #64748b;
  font-size: 13px;
  cursor: default;
  padding: 4px 16px;
}
</style>
