<template>
  <div class="expert-center">
    <div class="expert-center-header">
      <h2>我的专家</h2>
      <button @click="emit('close')" class="close-btn" title="关闭">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    </div>

    <div class="toolbar">
      <button class="primary-btn" @click="openCreate">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:16px;height:16px">
          <line x1="12" y1="5" x2="12" y2="19"></line>
          <line x1="5" y1="12" x2="19" y2="12"></line>
        </svg>
        新建专家
      </button>
      <input
        v-model="keyword"
        class="search-input"
        type="text"
        placeholder="搜索专家名称..."
        @keyup.enter="refresh"
      />
      <label class="checkbox-label">
        <input type="checkbox" v-model="includeDisabled" @change="refresh" />
        显示已删除
      </label>
    </div>

    <div class="expert-center-content">
      <div v-if="loading" class="state-block">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="error" class="state-block">
        <p class="error-text">{{ error }}</p>
        <button class="retry-btn" @click="refresh">重试</button>
      </div>

      <div v-else-if="experts.length === 0" class="state-block">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:42px;height:42px;opacity:.45">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
        <p>还没有专家，点击「新建专家」创建一个吧</p>
      </div>

      <div v-else class="expert-grid">
        <div v-for="item in experts" :key="item.export_agent_id" class="expert-card">
          <div class="expert-card-top">
            <div class="expert-avatar">{{ (item.export_name_cn || '?').slice(0, 1) }}</div>
            <div class="expert-title">
              <div class="expert-name">{{ item.export_name_cn }}</div>
              <div class="expert-meta">
                <span class="badge" :class="statusClass(item.agent_status)">
                  {{ statusText(item.agent_status) }}
                </span>
                <span class="version">v{{ item.export_version }}</span>
              </div>
            </div>
          </div>

          <p class="expert-desc">{{ truncate(item.export_prompt, 90) }}</p>

          <div v-if="item.export_features_prompt && item.export_features_prompt.length" class="feature-list">
            <span
              v-for="(f, i) in item.export_features_prompt.slice(0, 3)"
              :key="i"
              class="feature-chip"
              :title="f"
            >{{ truncate(f, 18) }}</span>
          </div>

          <div class="expert-card-actions">
            <button
              class="ghost-btn"
              :disabled="item.agent_status !== 'active'"
              @click="useExpert(item)"
            >去使用</button>
            <button class="ghost-btn" @click="openEdit(item)">编辑</button>
            <button class="ghost-btn danger" @click="confirmDelete(item)">删除</button>
          </div>
        </div>
      </div>
    </div>

    <!-- 创建 / 编辑 弹窗 -->
    <div v-if="dialogVisible" class="modal-mask" @click.self="closeDialog">
      <div class="modal">
        <div class="modal-header">
          <h3>{{ editing ? '编辑专家' : '新建专家' }}</h3>
          <button class="close-btn" @click="closeDialog">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>

        <div class="modal-body">
          <label class="field">
            <span class="field-label">专家名称 <em>*</em></span>
            <input v-model="form.nameCn" type="text" maxlength="50" placeholder="例如：量化策略专家" />
          </label>

          <label class="field">
            <span class="field-label">专家描述 <em>*</em></span>
            <textarea
              v-model="form.prompt"
              rows="8"
              maxlength="4000"
              :placeholder="promptPlaceholder"
            ></textarea>
            <span class="field-hint">
              <button type="button" class="link-btn" @click="fillTemplate">插入模板</button>
              <span>{{ form.prompt.length }} / 4000</span>
            </span>
          </label>

          <label class="field">
            <span class="field-label">分类标签（选填）</span>
            <input v-model="form.tagId" type="text" maxlength="64" placeholder="例如：finance" />
          </label>

          <p v-if="dialogError" class="error-text">{{ dialogError }}</p>
          <p v-if="submitting" class="field-hint">
            正在调用模型生成专家配置，请稍候（可能需要十几秒）...
          </p>
        </div>

        <div class="modal-footer">
          <button class="ghost-btn" :disabled="submitting" @click="closeDialog">取消</button>
          <button class="primary-btn" :disabled="submitting" @click="submit">
            <span v-if="submitting" class="btn-spinner"></span>
            {{ submitting ? '生成中...' : (editing ? '保存并生成新版本' : '创建') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import {
  addExpert,
  deleteExpert,
  listExperts,
  updateExpert,
} from '../api/expert.js'

const props = defineProps({
  /** 当前会话 ID（创建时溯源用） */
  sessionId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'use'])

/** 用户侧创建引导词模板（可后续迁移到提示词目录做成可配置） */
const CREATE_TEMPLATE =
  '帮我创建一个{name}专家，擅长 XXX。\n' +
  '我的经验是：[请补充你的行业背景、相关经验]\n' +
  '典型使用场景：[请描述你希望在什么情况下使用它]\n' +
  '期望输出：[请描述你希望它产出什么形式的结果]'

const STATUS_TEXT = {
  active: '可用',
  building: '生成中',
  failed: '生成失败',
  disabled: '已删除',
}

const experts = ref([])
const loading = ref(false)
const error = ref('')
const keyword = ref('')
const includeDisabled = ref(false)

const dialogVisible = ref(false)
const editing = ref(null)
const submitting = ref(false)
const dialogError = ref('')
const form = reactive({ nameCn: '', prompt: '', tagId: '' })

const promptPlaceholder = computed(() => CREATE_TEMPLATE.replace('{name}', 'XX'))

function statusText(status) {
  return STATUS_TEXT[status] || status || '-'
}

function statusClass(status) {
  return `status-${status || 'unknown'}`
}

function truncate(text, len) {
  const s = String(text || '')
  return s.length > len ? `${s.slice(0, len)}...` : s
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const data = await listExperts({
      keyword: keyword.value,
      includeDisabled: includeDisabled.value,
    })
    experts.value = data.items || []
  } catch (e) {
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  dialogError.value = ''
  form.nameCn = ''
  form.prompt = promptPlaceholder.value
  form.tagId = ''
  dialogVisible.value = true
}

function openEdit(item) {
  editing.value = item
  dialogError.value = ''
  form.nameCn = item.export_name_cn || ''
  form.prompt = item.export_prompt || ''
  form.tagId = item.tag_id || ''
  dialogVisible.value = true
}

function fillTemplate() {
  const name = form.nameCn || 'XX'
  form.prompt = CREATE_TEMPLATE.replace('{name}', name)
}

function closeDialog() {
  if (submitting.value) return
  dialogVisible.value = false
}

async function submit() {
  if (!form.nameCn.trim()) {
    dialogError.value = '请填写专家名称'
    return
  }
  if (!form.prompt.trim()) {
    dialogError.value = '请填写专家描述'
    return
  }
  submitting.value = true
  dialogError.value = ''
  try {
    if (editing.value) {
      await updateExpert({
        exportAgentId: editing.value.export_agent_id,
        nameCn: form.nameCn.trim(),
        prompt: form.prompt,
        tagId: form.tagId,
      })
    } else {
      await addExpert({
        nameCn: form.nameCn.trim(),
        prompt: form.prompt,
        tagId: form.tagId,
        sessionId: props.sessionId || null,
      })
    }
    dialogVisible.value = false
    await refresh()
  } catch (e) {
    dialogError.value = e.message || '提交失败'
  } finally {
    submitting.value = false
  }
}

async function confirmDelete(item) {
  const ok = window.confirm(
    `确定删除专家「${item.export_name_cn}」吗？\n（默认软删除，历史版本目录会保留）`
  )
  if (!ok) return
  try {
    await deleteExpert(item.export_agent_id, false)
    await refresh()
  } catch (e) {
    window.alert(e.message || '删除失败')
  }
}

function useExpert(item) {
  emit('use', item)
}

onMounted(refresh)
</script>

<style scoped>
.expert-center {
  /* 与 SkillCenter / AssetsPanel 一致：父级 .app-container 是 flex 容器，
     必须 flex:1 才能占满侧边栏右侧的全部空间（否则会被收缩成内容宽度）。 */
  flex: 1;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-width: 0;
  overflow: hidden;
  background: transparent;
  color: var(--text-primary);
}

.expert-center-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px;
  border-bottom: 1px solid #e2e8f0;
}

.expert-center-header h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.close-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-secondary);
  border-radius: 6px;
  cursor: pointer;
}

.close-btn svg {
  width: 18px;
  height: 18px;
}

.close-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 24px;
  border-bottom: 1px solid #e2e8f0;
  flex-wrap: wrap;
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border: none;
  border-radius: 8px;
  background: var(--primary-color);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
}

.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.search-input {
  flex: 1;
  min-width: 160px;
  padding: 7px 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
  outline: none;
}

.checkbox-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
}

.expert-center-content {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
}

.state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 220px;
  color: var(--text-secondary);
  font-size: 13px;
}

.spinner,
.btn-spinner {
  width: 18px;
  height: 18px;
  border: 2px solid var(--border-color);
  border-top-color: var(--primary-color);
  border-radius: 50%;
  animation: ec-spin 0.8s linear infinite;
}

.btn-spinner {
  width: 13px;
  height: 13px;
  border-top-color: #fff;
}

@keyframes ec-spin {
  to { transform: rotate(360deg); }
}

.expert-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 14px;
}

.expert-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-secondary);
}

.expert-card-top {
  display: flex;
  align-items: center;
  gap: 10px;
}

.expert-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--primary-color);
  color: #fff;
  font-size: 17px;
  flex-shrink: 0;
}

.expert-name {
  font-size: 14px;
  font-weight: 600;
}

.expert-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-secondary);
}

.badge {
  padding: 1px 7px;
  border-radius: 999px;
  background: var(--bg-tertiary);
}

.status-active { color: #16a34a; }
.status-building { color: #d97706; }
.status-failed { color: #dc2626; }
.status-disabled { color: var(--text-secondary); }

.expert-desc {
  margin: 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--text-secondary);
  min-height: 36px;
}

.feature-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.feature-chip {
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  font-size: 11px;
  color: var(--text-secondary);
}

.expert-card-actions {
  display: flex;
  gap: 8px;
  margin-top: auto;
  padding-top: 6px;
}

.ghost-btn {
  padding: 6px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.ghost-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
}

.ghost-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.ghost-btn.danger {
  color: #dc2626;
}

.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2000;
}

.modal {
  width: min(560px, 92vw);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: var(--bg-primary);
  border-radius: 14px;
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border-color);
}

.modal-header h3 {
  margin: 0;
  font-size: 15px;
}

.modal-body {
  padding: 16px 18px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.field-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.field-label em {
  color: #dc2626;
  font-style: normal;
}

.field input,
.field textarea {
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
  font-family: inherit;
  outline: none;
  box-sizing: border-box;
  resize: vertical;
}

.field input:focus,
.field textarea:focus {
  border-color: var(--primary-color);
}

.field-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-secondary);
}

.link-btn {
  border: none;
  background: transparent;
  color: var(--primary-color);
  font-size: 11px;
  cursor: pointer;
  padding: 0;
}

.error-text {
  margin: 0;
  color: #dc2626;
  font-size: 12px;
}

.retry-btn {
  padding: 6px 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: transparent;
  color: var(--text-primary);
  font-size: 12px;
  cursor: pointer;
}

.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 12px 18px;
  border-top: 1px solid var(--border-color);
}
</style>
