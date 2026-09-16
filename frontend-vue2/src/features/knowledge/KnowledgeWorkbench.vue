<template>
  <div class="ke-overlay">
    <section class="ke-shell">
      <header class="ke-topbar">
        <div class="ke-topbar-title">
          <button class="ke-icon-button" aria-label="返回对话" title="返回对话" @click="$emit('close')">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="19" y1="12" x2="5" y2="12"></line>
              <polyline points="12 19 5 12 12 5"></polyline>
            </svg>
          </button>
          <div>
            <h1>知识库</h1>
            <span>把资料变成可检索、可追溯的知识</span>
          </div>
        </div>
        <div v-if="enabled" class="ke-topbar-actions">
          <span class="ke-health" :class="ready ? 'ready' : 'degraded'"><i></i>{{ ready ? '知识服务正常' : '知识服务待就绪' }}</span>
        </div>
      </header>

      <div v-if="loading" class="ke-loading-page">
        <span class="ke-spinner"></span><p>正在连接知识服务…</p>
      </div>

      <div v-else-if="!enabled" class="ke-empty-page">
        <div class="ke-empty-illustration">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
          </svg>
        </div>
        <h2>知识库未启用</h2>
        <p>{{ emptyMessage }}</p>
        <button v-if="error" class="ke-button primary" @click="loadStatus">重试</button>
      </div>

      <div v-else class="ke-empty-page">
        <div class="ke-empty-illustration">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
          </svg>
        </div>
        <h2>知识服务已启用</h2>
        <p>{{ ready ? '知识服务运行正常。' : '知识服务正在启动，请稍后重试。' }}</p>
      </div>
    </section>
  </div>
</template>

<script>
import { getKnowledgeStatus } from './api.js'

export default {
  name: 'KnowledgeWorkbench',
  emits: ['close'],
  data() {
    return {
      loading: true,
      enabled: false,
      ready: false,
      unauthorized: false,
      error: '',
    }
  },
  computed: {
    emptyMessage() {
      if (this.unauthorized) return '当前账号无权访问知识库，请联系管理员确认权限。'
      if (this.error) return this.error
      return '知识服务尚未启用，主聊天仍可正常使用。请由管理员完成知识服务配置后再试。'
    },
  },
  mounted() {
    this.loadStatus()
  },
  methods: {
    async loadStatus() {
      this.loading = true
      this.error = ''
      this.unauthorized = false
      try {
        const status = await getKnowledgeStatus()
        this.enabled = Boolean(status && status.enabled)
        this.ready = Boolean(status && status.ready)
      } catch (e) {
        this.enabled = false
        const status = e && e.response ? e.response.status : 0
        if (status === 401 || status === 403) {
          this.unauthorized = true
        } else {
          this.error = e && e.message ? e.message : '加载知识服务状态失败'
        }
      } finally {
        this.loading = false
      }
    },
  },
}
</script>

<style scoped>
.ke-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: var(--bg-primary);
}

.ke-shell {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  color: var(--text-primary);
}

.ke-topbar {
  height: 64px;
  flex: none;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  z-index: 5;
}

.ke-topbar-title {
  display: flex;
  align-items: center;
  gap: 10px;
}

.ke-topbar-title h1 {
  font-size: 17px;
  line-height: 1.2;
  margin: 0;
  font-weight: 650;
}

.ke-topbar-title span {
  display: block;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11px;
}

.ke-topbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.ke-icon-button {
  width: 34px;
  height: 34px;
  flex: none;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 9px;
  display: grid;
  place-items: center;
  color: var(--text-secondary);
  cursor: pointer;
  transition: 0.18s ease;
}

.ke-icon-button:hover {
  background: var(--bg-tertiary);
}

.ke-icon-button svg {
  width: 18px;
  height: 18px;
}

.ke-health {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 9px;
  border-radius: 8px;
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-tertiary);
}

.ke-health i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #94a3b8;
}

.ke-health.ready i {
  background: #22c55e;
}

.ke-health.degraded i {
  background: #f59e0b;
}

.ke-button {
  height: 34px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 9px;
  padding: 0 14px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  transition: 0.18s ease;
}

.ke-button.primary {
  background: var(--accent-color);
  border-color: var(--accent-color);
  color: #fff;
}

.ke-button.primary:hover {
  filter: brightness(0.95);
}

.ke-loading-page,
.ke-empty-page {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: #858e88;
}

.ke-loading-page p {
  font-size: 11px;
}

.ke-empty-page h2 {
  font-size: 17px;
  color: var(--text-primary);
  margin: 15px 0 6px;
}

.ke-empty-page p {
  font-size: 11px;
  margin: 0 0 16px;
  max-width: 420px;
  line-height: 1.7;
}

.ke-empty-illustration {
  width: 52px;
  height: 52px;
  border-radius: 16px;
  display: grid;
  place-items: center;
  color: var(--accent-color);
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

.ke-empty-illustration svg {
  width: 26px;
  height: 26px;
}

.ke-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--border-color);
  border-top-color: var(--accent-color);
  border-radius: 50%;
  animation: ke-spin 0.75s linear infinite;
}

@keyframes ke-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
