<!--
  技能类别管理弹层：
  - 新建类别 / 重命名 / 删除 / 上移下移排序
  - 每行显示该类别下的技能数量；兜底类别「通用技能」只读（不可改名/删除）
  - 变动即时落库（后端持久化），通过 changed 事件让列表刷新分组
-->
<template>
  <div class="category-manager-mask" @mousedown="overlayPress = markOverlayPress($event)"
  @click.self="overlayClickCloses($event, overlayPress, () => $emit('close'))">
    <div class="category-manager" role="dialog" aria-modal="true" aria-label="管理技能类别">
      <div class="manager-header">
        <h3>管理分类</h3>
        <button class="icon-btn" aria-label="关闭" @click="$emit('close')">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>
      </div>

      <div class="manager-body">
        <div class="create-row">
          <input
            v-model="newName"
            type="text"
            maxlength="24"
            placeholder="新建分类，如「投资理财」"
            aria-label="新分类名称"
            @keyup.enter="handleCreate"
          />
          <button class="action-btn-primary" :disabled="busy || !newName.trim()" @click="handleCreate">
            新建
          </button>
        </div>

        <ul class="category-list">
          <li v-for="(item, index) in rows" :key="item.id" class="category-row">
            <div class="row-main">
              <input
                v-if="editingId === item.id"
                v-model="editingName"
                type="text"
                maxlength="24"
                class="rename-input"
                aria-label="分类名称"
                @keyup.enter="handleRename(item)"
                @keyup.esc="cancelRename"
              />
              <span v-else class="row-name">{{ item.name }}</span>

              <span class="row-count">{{ item.count }} 个技能</span>
              <span v-if="item.isDefault" class="row-tag">默认</span>
            </div>

            <div class="row-actions">
              <template v-if="editingId === item.id">
                <button class="text-btn" :disabled="busy" @click="handleRename(item)">保存</button>
                <button class="text-btn" @click="cancelRename">取消</button>
              </template>
              <template v-else>
                <button
                  class="icon-btn"
                  aria-label="上移"
                  :disabled="busy || index === 0"
                  @click="move(index, -1)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <line x1="12" y1="19" x2="12" y2="5" />
                    <polyline points="5 12 12 5 19 12" />
                  </svg>
                </button>
                <button
                  class="icon-btn"
                  aria-label="下移"
                  :disabled="busy || index === rows.length - 1"
                  @click="move(index, 1)"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <polyline points="19 12 12 19 5 12" />
                  </svg>
                </button>
                <button
                  v-if="!item.isDefault"
                  class="text-btn"
                  :disabled="busy"
                  @click="startRename(item)"
                >
                  重命名
                </button>
                <button
                  v-if="!item.isDefault"
                  class="text-btn danger"
                  :disabled="busy"
                  @click="confirmingId === item.id ? handleDelete(item) : (confirmingId = item.id)"
                >
                  {{ confirmingId === item.id ? '确认删除' : '删除' }}
                </button>
              </template>
            </div>
          </li>
        </ul>

        <p class="manager-tip">
          删除分类后，其下技能会回到「按内容自动归类」；单个技能的归属可在技能卡片的分类标签上修改。
        </p>
      </div>

      <Transition name="toast">
        <div v-if="toast.show" class="toast" :class="toast.type" role="status" aria-live="polite">
          {{ toast.message }}
        </div>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { overlayClickCloses, markOverlayPress } from '../utils/overlayClick.js'
import {
  createSkillCategory,
  renameSkillCategory,
  deleteSkillCategory,
  reorderSkillCategories,
} from '../api/skills.js'

const overlayPress = ref(null)
const props = defineProps({
  /** 用户类别（含内置预置，按 order 排序） */
  categories: { type: Array, default: () => [] },
  /** 虚拟兜底类别：只读展示 */
  defaultCategory: { type: Object, default: () => ({ id: 'default', name: '通用技能' }) },
  /** 类别 id -> 技能数量 */
  counts: { type: Object, default: () => ({}) },
})

const emit = defineEmits(['close', 'changed'])

const newName = ref('')
const editingId = ref('')
const editingName = ref('')
const confirmingId = ref('')
const busy = ref(false)
const toast = ref({ show: false, message: '', type: 'success' })

/** 展示行：用户类别 + 兜底类别（固定末尾，只读） */
const rows = computed(() => [
  ...props.categories.map((item) => ({
    id: item.id,
    name: item.name,
    count: props.counts[item.id] || 0,
    isDefault: false,
  })),
  {
    id: props.defaultCategory.id,
    name: props.defaultCategory.name,
    count: props.counts[props.defaultCategory.id] || 0,
    isDefault: true,
  },
])

function showToast(message, type = 'success') {
  toast.value = { show: true, message, type }
  setTimeout(() => { toast.value = { ...toast.value, show: false } }, 2400)
}

/** 统一收口请求：busy 期间禁止并发操作，失败弹出后端 detail */
async function run(action, successMessage) {
  busy.value = true
  try {
    await action()
    showToast(successMessage)
    emit('changed', successMessage)
    return true
  } catch (e) {
    showToast(e.message || '操作失败', 'error')
    return false
  } finally {
    busy.value = false
  }
}

async function handleCreate() {
  const name = newName.value.trim()
  if (!name) return
  const ok = await run(() => createSkillCategory(name), `分类「${name}」已创建`)
  if (ok) newName.value = ''
}

function startRename(item) {
  editingId.value = item.id
  editingName.value = item.name
}

function cancelRename() {
  editingId.value = ''
  editingName.value = ''
}

async function handleRename(item) {
  const name = editingName.value.trim()
  if (!name || name === item.name) return cancelRename()
  const ok = await run(
    () => renameSkillCategory(item.id, name),
    `分类已重命名为「${name}」`
  )
  if (ok) cancelRename()
}

async function handleDelete(item) {
  confirmingId.value = ''
  await run(() => deleteSkillCategory(item.id), `分类「${item.name}」已删除`)
}

/** 上移 / 下移：只交换展示顺序，整体顺序提交后端 */
async function move(index, delta) {
  const target = index + delta
  if (target < 0 || target >= rows.value.length) return
  const ids = rows.value.filter((row) => !row.isDefault).map((row) => row.id)
  const from = ids.indexOf(rows.value[index].id)
  const to = ids.indexOf(rows.value[target].id)
  if (from < 0 || to < 0) return
  const next = [...ids]
  next.splice(to, 0, next.splice(from, 1)[0])
  await run(() => reorderSkillCategories(next), '分类顺序已更新')
}
</script>

<style scoped>
.category-manager-mask {
  position: fixed;
  inset: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(15, 23, 42, 0.45);
}

.category-manager {
  position: relative;
  width: min(460px, 100%);
  max-height: min(560px, 88vh);
  display: flex;
  flex-direction: column;
  border-radius: 14px;
  border: 1px solid var(--border-color);
  background: var(--bg-surface);
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.24);
}

.manager-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-color);
}

.manager-header h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.manager-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px 16px 16px;
}

.create-row {
  display: flex;
  gap: 8px;
}

.create-row input {
  flex: 1;
  min-width: 0;
  height: 36px;
  padding: 0 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
}

.create-row input:focus {
  outline: none;
  border-color: var(--accent-color);
}

.category-list {
  margin: 14px 0 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.category-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-secondary);
  transition: border-color 0.18s ease;
}

.category-row:hover {
  border-color: color-mix(in srgb, var(--accent-color) 30%, var(--border-color));
}

.row-main {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.row-name {
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rename-input {
  flex: 1;
  min-width: 0;
  padding: 4px 8px;
  border: 1px solid var(--accent-color);
  border-radius: 6px;
  background: var(--bg-secondary);
  color: var(--text-primary);
  font-size: 13px;
}

.row-count {
  font-size: 11.5px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.row-tag {
  padding: 1px 6px;
  border-radius: 999px;
  border: 1px solid var(--border-color);
  font-size: 10.5px;
  color: var(--text-secondary);
}

.row-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.icon-btn:hover:not(:disabled) {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.icon-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.icon-btn svg {
  width: 14px;
  height: 14px;
}

.text-btn {
  padding: 4px 6px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
}

.text-btn:hover:not(:disabled) {
  background: var(--bg-secondary);
  color: var(--text-primary);
}

.text-btn.danger {
  color: #dc2626;
}

.manager-tip {
  margin: 14px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.action-btn-primary {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 36px;
  padding: 0 16px;
  border: none;
  border-radius: 10px;
  background: var(--accent-color);
  color: #fff;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}

.action-btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.toast {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  padding: 8px 14px;
  border-radius: 8px;
  font-size: 12.5px;
  color: #fff;
  background: #1e293b;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.28);
  z-index: 2;
}

.toast.error {
  background: #b91c1c;
}

.toast-enter-active,
.toast-leave-active {
  transition: opacity 0.18s ease, transform 0.18s ease;
}

.toast-enter-from,
.toast-leave-to {
  opacity: 0;
  transform: translate(-50%, 6px);
}
</style>
