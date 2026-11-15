<template>
  <div class="approval-panel">
    <div class="panel-header">
      <h3>权限设置</h3>
      <p class="panel-desc">控制 AI 执行<b>删除、修改、新增</b>操作时，是否需要你确认后再生效。</p>
    </div>

    <div v-if="loading" class="loading-state"><div class="spinner"></div></div>

    <template v-else>
      <!-- 服务端关闭了 HITL：两个选项都没有意义，置灰并说明原因 -->
      <div v-if="!available" class="note locked">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <rect x="4" y="10" width="16" height="10" rx="2" />
          <path d="M8 10V7a4 4 0 0 1 8 0v3" />
        </svg>
        <div>{{ lockedReason || '当前部署未启用人工审批，无法选择审批模式' }}</div>
      </div>

      <div class="mode-list" role="radiogroup" aria-label="审批模式">
        <!-- 人工介入审批 -->
        <div
          class="mode-card"
          :class="{ on: draft === 'manual', disabled: !available }"
          role="radio"
          :aria-checked="draft === 'manual'"
          :tabindex="available ? 0 : -1"
          @click="pick('manual')"
          @keydown.enter.prevent="pick('manual')"
          @keydown.space.prevent="pick('manual')"
        >
          <span class="radio" aria-hidden="true"></span>
          <div class="mode-body">
            <div class="mode-title">人工介入审批</div>
            <p class="mode-sub">
              删除文件、执行删除类命令、修改记忆、新建或修改专家等操作会<b>先暂停</b>，
              等你点击「批准」后才执行。也可以在审批卡片上拒绝，或改参数后重试。
            </p>
            <div class="mode-meta">
              <span>每次操作多一步确认</span>
              <span>误删风险最低</span>
            </div>
          </div>
        </div>

        <!-- 允许自动审批 -->
        <div
          class="mode-card"
          :class="{ on: draft === 'auto', disabled: !available }"
          role="radio"
          :aria-checked="draft === 'auto'"
          :tabindex="available ? 0 : -1"
          @click="pick('auto')"
          @keydown.enter.prevent="pick('auto')"
          @keydown.space.prevent="pick('auto')"
        >
          <span class="radio" aria-hidden="true"></span>
          <div class="mode-body">
            <div class="mode-title">
              允许自动审批
              <span class="badge">有风险</span>
            </div>
            <p class="mode-sub">
              系统代替你直接批准：删除文件、终端命令（含 <code>rm</code> / <code>del</code>）、
              新建或修改自己的专家、创建定时任务、知识库原文落盘、MCP 查询都<b>不再打断对话</b>。
              适合批量整理文件、跑数据清洗等明确可控的任务。
            </p>
            <div class="mode-meta">
              <span>不打断，连续执行</span>
              <span>删除不可撤销</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 自动审批的固定范围（不做细分开关） -->
      <div v-if="draft === 'auto'" class="scope">
        <div class="scope-head">
          <span class="scope-title">自动审批的范围</span>
          <span class="scope-hint">固定范围，不提供细分开关</span>
        </div>
        <div class="scope-cols">
          <div class="scope-col">
            <div class="scope-col-head ok">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              直接执行
            </div>
            <ul>
              <li v-for="item in allowItems" :key="item.label">
                {{ item.label }}
                <em v-if="item.hint">{{ item.hint }}</em>
              </li>
            </ul>
          </div>
          <div class="scope-col">
            <div class="scope-col-head no">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                <circle cx="12" cy="12" r="9" />
                <line x1="6" y1="6" x2="18" y2="18" />
              </svg>
              仍然拦截
            </div>
            <ul>
              <li v-for="item in blockItems" :key="item.label">
                {{ item.label }}
                <em v-if="item.hint">{{ item.hint }}</em>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <!-- 人工介入：说明这条模式下会拦哪些 -->
      <div v-else class="scope">
        <div class="scope-head">
          <span class="scope-title">会停下来等你确认的操作</span>
          <span class="scope-hint">点「批准」后才执行，点「拒绝」则放弃</span>
        </div>
        <ul class="pending-list">
          <li v-for="item in manualItems" :key="item.label">
            {{ item.label }}
            <em v-if="item.hint">{{ item.hint }}</em>
          </li>
        </ul>
        <div class="scope-sub">以下操作会被直接拒绝，不会进入审批：</div>
        <ul class="pending-list">
          <li v-for="item in blockItems" :key="item.label">
            {{ item.label }}
            <em v-if="item.hint">{{ item.hint }}</em>
          </li>
        </ul>
      </div>

      <!-- 风险提示（仅自动审批） -->
      <div v-if="draft === 'auto'" class="note warn">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        <div>
          <b>开启后 AI 将不再征求你的同意。</b>
          <ul>
            <li>被删除的文件无法从对话中恢复，请确认工作区里没有重要数据。</li>
            <li>
              删除工具可以<b>递归删除整个目录</b>，终端里的 <code>rm</code> / <code>del</code>
              也会直接执行（<code>rm -r</code> 删目录仍在策略层被拒绝）。
            </li>
            <li>新建的定时任务会到点自动运行，不再二次确认；请在「定时任务」里复核已创建的任务。</li>
            <li>知识库原文落盘会把原文<b>直接复制进你的工作区</b>，不再询问，敏感文档请先确认。</li>
            <li>放行的操作仍会在对话里显示工具调用记录，只是不再出现审批卡片。</li>
          </ul>
        </div>
      </div>

      <!-- 生效说明 -->
      <div class="note info">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="12" cy="12" r="9" />
          <line x1="12" y1="11" x2="12" y2="16" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
        <div>
          保存后<b>立即生效</b>：正在进行的回答不受影响，从下一条消息开始按新模式执行。
          设置跟随你的账号，新建会话与已有会话都适用。
        </div>
      </div>

      <!-- 保存栏 -->
      <div class="save-bar">
        <span v-if="saveError" class="save-error">{{ saveError }}</span>
        <span v-else-if="savedTip" class="save-ok">已保存，下一条消息生效</span>
        <span v-else-if="dirty" class="save-hint">有未保存的修改</span>
        <button class="save-btn" :disabled="!canSave" @click="save">
          {{ saving ? '保存中...' : '保存' }}
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import {
  getApprovalSettings,
  updateApprovalSettings,
} from '../services/approval.js'

// 审批项文案：key 与后端 agent/hitl.py 的 AUTO_APPROVED_APPROVAL_TOOLS 完全同名，
// 顺序即手动模式下「会停下来等你确认」的展示顺序。服务端只下发工具名，文案在前端维护；
// 新增放行项时这里补一条即可（漏补会退化成显示工具名，不会静默消失）。
const APPROVAL_COPY = {
  execute: { label: '终端命令（含 rm / del 删除）', hint: '沙箱内执行；rm -r 删目录仍被拒绝' },
  delete: { label: '删除文件或目录', hint: '含递归删除，删掉不可恢复' },
  save_expert: { label: '新建、修改自己的专家', hint: '只能动自己名下的专家' },
  create_scheduled_task: { label: '创建定时任务', hint: '到点自动执行，不再二次确认' },
  access_knowledge_original: { label: '知识库原文落盘（materialize）', hint: '会把知识库原文写进你的工作区' },
}

// 自动审批下不在服务端清单里、但同样不走审批的两类：
// MCP 整段放行（后端 AUTO_APPROVES_MCP_TOOLS）、敏感路径写入不生成规则（agent/workspace.py）。
const AUTO_EXTRA_ALLOW_ITEMS = [
  { label: 'MCP 工具（含查询 / SQL 查询）', hint: '当前接入的 MCP 只提供查询能力' },
  { label: '改写自己的记忆与自己安装的技能', hint: '他人的数据仍然只读' },
]

// 手动模式下额外要确认的审批项：MCP 写操作按名字 / 服务端标注判定，敏感写入看部署开关。
const MANUAL_EXTRA_ITEMS = [
  { label: 'MCP 写操作（建表 / 删表 / SQL）', hint: '按名字或服务端标注判定，读操作不拦' },
  { label: '改写自己的记忆与自己安装的技能', hint: '部署开启敏感写入审批时才拦截' },
]

// 两种模式下都执行不了的操作：策略层 / 权限层直接拒绝，与审批开关无关。
const BLOCK_ITEMS = [
  { label: '目录递归删除 rm -r / rmdir', hint: '策略层直接拒绝，删目录请用「删除文件或目录」' },
  { label: '他人的专家、他人创建的数据', hint: '权限层直接拒绝，与审批开关无关' },
  { label: '公共技能（系统预置）', hint: '只读，不可改不可删' },
]

const loading = ref(true)
const saving = ref(false)
const available = ref(true)
const lockedReason = ref('')
// 服务端已保存的模式
const mode = ref('manual')
// 界面上选中的模式（未保存）
const draft = ref('manual')
const autoApprovedTools = ref([])
const savedTip = ref(false)
const saveError = ref('')

const dirty = computed(() => draft.value !== mode.value)
const canSave = computed(() => available.value && dirty.value && !saving.value)

// 服务端下发的审批名单（与 config.yaml 的 hitl.tools 同源）→ 文案；
// 未知名字退化成显示工具名，不会静默消失。
const approvalItems = computed(() =>
  (autoApprovedTools.value || []).map((name) => APPROVAL_COPY[name] || { label: name })
)

// auto：名单里的每一类都直接执行，另补两条「不在名单里、但同样不走审批」的说明
const allowItems = computed(() => approvalItems.value.concat(AUTO_EXTRA_ALLOW_ITEMS))

// manual：名单里的每一类都要确认，另加 MCP 写工具与会话内的记忆 / 技能写入
const manualItems = computed(() => approvalItems.value.concat(MANUAL_EXTRA_ITEMS))

const blockItems = BLOCK_ITEMS

function applyServerState(data) {
  const next = data && data.mode === 'auto' ? 'auto' : 'manual'
  mode.value = next
  draft.value = next
  available.value = data ? data.available !== false : true
  lockedReason.value = (data && data.locked_reason) || ''
  autoApprovedTools.value = Array.isArray(data && data.auto_approved_tools)
    ? data.auto_approved_tools
    : []
}

async function load() {
  loading.value = true
  saveError.value = ''
  try {
    const data = await getApprovalSettings()
    applyServerState(data)
  } catch (e) {
    console.error('加载权限设置失败:', e)
    saveError.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function pick(next) {
  if (!available.value || saving.value) return
  savedTip.value = false
  saveError.value = ''
  draft.value = next
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  saveError.value = ''
  try {
    const data = await updateApprovalSettings(draft.value)
    applyServerState(data)
    savedTip.value = true
    setTimeout(() => { savedTip.value = false }, 3000)
  } catch (e) {
    console.error('保存权限设置失败:', e)
    saveError.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.approval-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 760px;
  /* 弹窗内容区（.settings-content）才是滚动容器，且 .content-panel 是 height:100%，
     面板内容溢出它的盒模型，滚动到最底部时保存栏会紧贴弹窗底边。
     所以留白必须由面板自己兜住（这 16px 会算进可滚动内容里）。 */
  padding-bottom: 16px;
}

.panel-header h3 {
  margin: 0 0 4px 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-desc {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.spinner {
  width: 28px;
  height: 28px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: approval-spin 1s linear infinite;
}

@keyframes approval-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* 模式卡片 */
.mode-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.mode-card {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  padding: 14px 16px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  background: var(--bg-secondary, #fff);
  cursor: pointer;
  outline: none;
  transition: border-color 0.18s, background 0.18s, box-shadow 0.18s;
}

.mode-card:hover {
  border-color: color-mix(in srgb, var(--accent-color, #0ea5e9) 45%, var(--border-color, #e2e8f0));
}

.mode-card:focus-visible {
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color, #0ea5e9) 22%, transparent);
}

.mode-card.on {
  border-color: var(--accent-color, #0ea5e9);
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 8%, transparent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent-color, #0ea5e9) 14%, transparent);
}

.mode-card.disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.radio {
  flex: 0 0 16px;
  width: 16px;
  height: 16px;
  margin-top: 2px;
  border-radius: 50%;
  border: 1.5px solid #cbd5e1;
  background: #fff;
  position: relative;
}

.mode-card.on .radio {
  border-color: var(--accent-color, #0ea5e9);
  border-width: 5px;
}

.mode-body {
  flex: 1;
  min-width: 0;
}

.mode-title {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 5px;
  color: #b45309;
  background: #fef3c7;
}

.mode-sub {
  margin: 5px 0 0;
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--text-secondary);
}

.mode-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.mode-meta span::before {
  content: "·";
  margin-right: 5px;
  font-weight: 700;
  color: var(--accent-color, #0ea5e9);
}

/* 范围清单 */
.scope {
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 10px;
  overflow: hidden;
  background: var(--bg-secondary, #fff);
}

.scope-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding: 10px 14px;
  background: var(--bg-tertiary, #f8fafc);
  border-bottom: 1px solid var(--border-color, #e2e8f0);
}

.scope-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.scope-hint {
  font-size: 11.5px;
  color: var(--text-secondary);
}

.scope-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
}

.scope-col {
  padding: 12px 16px 14px;
}

.scope-col + .scope-col {
  border-left: 1px solid var(--border-color, #e2e8f0);
}

.scope-col-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  margin-bottom: 8px;
}

.scope-col-head svg {
  width: 14px;
  height: 14px;
  flex: 0 0 14px;
}

.scope-col-head.ok { color: #047857; }
.scope-col-head.no { color: #b45309; }

.scope-col ul,
.pending-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.pending-list {
  padding: 12px 16px 14px;
}

/* 手动模式里「直接拒绝」那一段的小标题 */
.scope-sub {
  margin-top: 12px;
  padding: 10px 16px 0;
  border-top: 1px solid var(--border-color, #e2e8f0);
  font-size: 11.5px;
  color: var(--text-secondary);
}

.scope-col li,
.pending-list li {
  position: relative;
  padding-left: 14px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.scope-col li:last-child,
.pending-list li:last-child {
  margin-bottom: 0;
}

.scope-col li::before,
.pending-list li::before {
  content: "";
  position: absolute;
  left: 2px;
  top: 7px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: var(--text-secondary);
  opacity: 0.5;
}

.scope-col li em,
.pending-list li em {
  display: block;
  font-style: normal;
  font-size: 11.5px;
  color: var(--text-secondary);
}

/* 提示块 */
.note {
  display: flex;
  gap: 9px;
  padding: 11px 13px;
  border-radius: 9px;
  font-size: 12.5px;
  line-height: 1.65;
  border: 1px solid transparent;
}

.note svg {
  width: 15px;
  height: 15px;
  flex: 0 0 15px;
  margin-top: 2px;
}

.note ul {
  margin: 6px 0 0;
  padding-left: 18px;
}

.note li {
  margin-bottom: 3px;
}

.note li:last-child {
  margin-bottom: 0;
}

.mode-sub code,
.note code {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(100, 116, 139, 0.16);
  font-size: 0.92em;
}

.note.warn {
  color: #92400e;
  background: #fffbeb;
  border-color: #fde68a;
}

.note.info {
  color: var(--text-secondary);
  background: var(--bg-tertiary, #f1f5f9);
  border-color: var(--border-color, #e2e8f0);
}

.note.info b {
  color: var(--text-primary);
}

.note.locked {
  color: #1e40af;
  background: #eff6ff;
  border-color: #bfdbfe;
  align-items: flex-start;
}

/* 保存栏 */
.save-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 4px;
}

.save-hint,
.save-ok,
.save-error {
  font-size: 12.5px;
}

.save-hint { color: var(--text-secondary); }
.save-ok { color: #047857; }
.save-error { color: #b91c1c; }

.save-btn {
  font: inherit;
  font-size: 13px;
  font-weight: 500;
  padding: 7px 20px;
  border-radius: 8px;
  border: 1px solid var(--accent-color, #0ea5e9);
  background: var(--accent-color, #0ea5e9);
  color: #fff;
  cursor: pointer;
  transition: opacity 0.18s;
}

.save-btn:hover:not(:disabled) {
  opacity: 0.9;
}

.save-btn:disabled {
  border-color: var(--border-color, #e2e8f0);
  background: var(--bg-tertiary, #f1f5f9);
  color: var(--text-secondary);
  cursor: not-allowed;
}

/* 窄屏：两栏清单改为上下排列 */
@media (max-width: 40rem) {
  .scope-cols {
    grid-template-columns: 1fr;
  }

  .scope-col + .scope-col {
    border-left: none;
    border-top: 1px solid var(--border-color, #e2e8f0);
  }

  .save-bar {
    flex-wrap: wrap;
  }
}

/* 深色主题 */
.easy-agent-root[data-theme="dark"] .mode-card {
  background: #1e293b;
  border-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .mode-card.on {
  background: rgba(14, 165, 233, 0.14);
  border-color: #0ea5e9;
}

.easy-agent-root[data-theme="dark"] .radio {
  background: #0f172a;
  border-color: #475569;
}

.easy-agent-root[data-theme="dark"] .badge {
  color: #fbbf24;
  background: rgba(180, 83, 9, 0.28);
}

.easy-agent-root[data-theme="dark"] .scope {
  background: #1e293b;
  border-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .scope-head {
  background: #172033;
  border-bottom-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .scope-col + .scope-col {
  border-left-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .scope-sub {
  border-top-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .scope-col-head.ok { color: #34d399; }
.easy-agent-root[data-theme="dark"] .scope-col-head.no { color: #fbbf24; }

.easy-agent-root[data-theme="dark"] .note.warn {
  color: #fcd34d;
  background: rgba(180, 83, 9, 0.16);
  border-color: rgba(245, 158, 11, 0.4);
}

.easy-agent-root[data-theme="dark"] .note.info {
  background: #172033;
  border-color: #30363d;
}

.easy-agent-root[data-theme="dark"] .note.locked {
  color: #93c5fd;
  background: rgba(30, 64, 175, 0.2);
  border-color: rgba(59, 130, 246, 0.4);
}
</style>
