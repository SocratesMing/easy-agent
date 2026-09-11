<template>
  <div class="pm-overlay" @click.self="$emit('close')">
    <section class="pm-panel" role="dialog" aria-modal="true" aria-labelledby="personnel-title">
      <header class="pm-header">
        <div class="pm-heading">
          <span class="pm-heading-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
              <circle cx="9" cy="7" r="4" />
              <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
            </svg>
          </span>
          <div>
            <h2 id="personnel-title">人员与部门</h2>
            <p>统一维护账号、部门归属，并指定谁可创建和管理团队空间</p>
          </div>
        </div>
        <button class="pm-icon-btn" type="button" title="关闭" aria-label="关闭人员管理" @click="$emit('close')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="m18 6-12 12M6 6l12 12" />
          </svg>
        </button>
      </header>

      <div class="pm-toolbar">
        <label class="pm-search">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
          <input v-model="keyword" type="search" placeholder="搜索账号、姓名、工号或邮箱" aria-label="搜索人员" />
        </label>
        <label class="pm-filter">
          <span>状态</span>
          <select v-model="accountStatus" aria-label="按账号状态筛选">
            <option value="">全部</option>
            <option value="active">启用</option>
            <option value="disabled">停用</option>
          </select>
        </label>
        <label class="pm-filter pm-department-filter">
          <span>部门</span>
          <input v-model="departmentId" placeholder="部门编号" aria-label="按部门编号筛选" />
        </label>
        <span class="pm-toolbar-spacer"></span>
        <button class="pm-btn pm-quiet" type="button" :disabled="downloadingTemplate" @click="downloadTemplate">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" />
          </svg>
          {{ downloadingTemplate ? '下载中…' : '下载模板' }}
        </button>
        <button class="pm-btn pm-quiet" type="button" @click="openImport">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 21V9m0 0 4 4m-4-4-4 4M5 3h14" />
          </svg>
          Excel 导入
        </button>
        <button class="pm-btn pm-primary" type="button" @click="openCreate">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14" />
          </svg>
          手动添加
        </button>
      </div>

      <section class="pm-team-access-guide" aria-label="团队空间权限说明">
        <span aria-hidden="true">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
            <path d="m9 12 2 2 4-4" />
          </svg>
        </span>
        <div>
          <strong>团队空间创建与管理授权</strong>
          <p>在人员列表的“团队空间权限”列打开开关，该账号即可创建并管理所属部门的团队知识库。admin 默认拥有全局权限，无需给自己授权。</p>
          <small>这是全局团队空间权限；单个知识库的查看者、维护者和管理员，仍在该知识库的“成员”中配置。</small>
        </div>
      </section>

      <div v-if="noticeText" class="pm-notice" :class="noticeType" role="status">
        <svg v-if="noticeType === 'success'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="m5 12 4 4L19 6" />
        </svg>
        <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="9" /><path d="M12 8v5m0 3h.01" />
        </svg>
        <span>{{ noticeText }}</span>
        <button type="button" aria-label="关闭提示" @click="noticeText = ''">×</button>
      </div>

      <div class="pm-summary">
        <div><strong>{{ total }}</strong><span>人员总数</span></div>
        <i></i>
        <div><strong>{{ activeCount }}</strong><span>当前列表已启用</span></div>
        <i></i>
        <div><strong>{{ teamManagerCount }}</strong><span>团队空间管理员</span></div>
        <span class="pm-summary-tip">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" /><path d="m9 12 2 2 4-4" />
          </svg>
          仅 admin 可配置，后端会再次校验权限
        </span>
      </div>

      <main class="pm-content">
        <div class="pm-table-wrap">
          <table class="pm-table">
            <thead>
              <tr>
                <th>账号 / 姓名</th>
                <th>工号</th>
                <th>部门</th>
                <th>岗位 / 联系方式</th>
                <th>信息来源</th>
                <th>团队空间权限</th>
                <th>状态</th>
                <th class="pm-actions-head">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="loading">
                <td colspan="8">
                  <div class="pm-loading"><span></span>正在加载人员信息…</div>
                </td>
              </tr>
              <tr v-else-if="users.length === 0">
                <td colspan="8">
                  <div class="pm-empty">
                    <span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">
                        <circle cx="9" cy="7" r="4" /><path d="M3 21v-2a6 6 0 0 1 12 0v2m2-10h5m-2.5-2.5v5" />
                      </svg>
                    </span>
                    <strong>{{ hasFilters ? '没有找到匹配人员' : '还没有人员数据' }}</strong>
                    <p>{{ hasFilters ? '请调整搜索词或筛选条件。' : '可手动添加，也可通过 Excel 批量导入。' }}</p>
                    <button v-if="hasFilters" class="pm-text-btn" type="button" @click="clearFilters">清除筛选</button>
                  </div>
                </td>
              </tr>
              <template v-else>
                <tr v-for="user in users" :key="user.user_id || user.username">
                  <td>
                    <div class="pm-person">
                      <span>{{ avatarText(user) }}</span>
                      <div><strong>{{ user.display_name || '未填写姓名' }}</strong><small>{{ user.username }}</small></div>
                    </div>
                  </td>
                  <td><span class="pm-cell-main">{{ user.employee_id || '—' }}</span></td>
                  <td>
                    <div class="pm-stacked"><strong>{{ user.department_name || '未填写部门名称' }}</strong><small>{{ user.department_id || '—' }}</small></div>
                  </td>
                  <td>
                    <div class="pm-stacked"><strong>{{ user.position || '—' }}</strong><small>{{ contactText(user) }}</small></div>
                  </td>
                  <td><span class="pm-source" :title="user.personnel_source || '未记录'">{{ user.personnel_source || '未记录' }}</span></td>
                  <td>
                    <span v-if="user.username === 'admin'" class="pm-access-note global">全局管理</span>
                    <button
                      v-else
                      class="pm-permission-switch"
                      :class="{ active: isTeamManager(user), pending: teamPermissionUserId === user.user_id }"
                      type="button"
                      role="switch"
                      :aria-checked="isTeamManager(user) ? 'true' : 'false'"
                      :disabled="teamPermissionUserId === user.user_id || (!isTeamManager(user) && !canGrantTeamManager(user))"
                      :title="teamPermissionTitle(user)"
                      @click="toggleTeamPermission(user)"
                    >
                      <i><span></span></i>
                      <strong>{{ isTeamManager(user) ? '可创建/管理' : '仅查看' }}</strong>
                    </button>
                  </td>
                  <td><span class="pm-status" :class="user.account_status"><i></i>{{ statusLabel(user.account_status) }}</span></td>
                  <td>
                    <div class="pm-row-actions">
                      <button
                        type="button"
                        :disabled="user.username === 'admin'"
                        :title="user.username === 'admin' ? '系统管理员账号不可在此修改' : '编辑人员'"
                        @click="openEdit(user)"
                      >编辑</button>
                      <button
                        type="button"
                        :disabled="user.username === 'admin' || resettingUsername === user.username"
                        :title="user.username === 'admin' ? '系统管理员账号不可在此重置密码' : '重置后密码为 123456'"
                        @click="resetPassword(user)"
                      >
                        {{ user.username === 'admin' ? '不可重置' : (resettingUsername === user.username ? '重置中…' : '重置密码') }}
                      </button>
                    </div>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </main>

      <footer class="pm-footer">
        <span>人员数据保存在本地 MySQL，用于账号登录、部门权限与知识库隔离。</span>
        <div class="pm-pagination" aria-label="人员列表分页">
          <span v-if="total">第 {{ pageStart }}–{{ pageEnd }} 条，共 {{ total }} 条</span>
          <button class="pm-text-btn" type="button" :disabled="loading || page <= 1" @click="changePage(page - 1)">上一页</button>
          <strong>{{ page }} / {{ totalPages }}</strong>
          <button class="pm-text-btn" type="button" :disabled="loading || page >= totalPages" @click="changePage(page + 1)">下一页</button>
          <button class="pm-text-btn" type="button" :disabled="loading" @click="loadUsers">刷新</button>
        </div>
      </footer>

      <div v-if="showEditor" class="pm-dialog-backdrop" @click.self="closeEditor">
        <section class="pm-dialog pm-editor" role="dialog" aria-modal="true" aria-labelledby="personnel-editor-title">
          <header class="pm-dialog-header">
            <div>
              <h3 id="personnel-editor-title">{{ editingUserId ? '编辑人员' : '手动添加人员' }}</h3>
              <p>{{ editingUserId ? '修改后将同步影响部门权限判定。' : '新账号初始密码为 123456，请按本单位密码管理流程及时更换。' }}</p>
            </div>
            <button class="pm-icon-btn" type="button" title="关闭" aria-label="关闭人员编辑" @click="closeEditor"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m18 6-12 12M6 6l12 12" /></svg></button>
          </header>
          <form class="pm-form" @submit.prevent="saveUser">
            <label>
              <span>账号 <b>*</b></span>
              <input v-model="form.username" :disabled="Boolean(editingUserId)" autocomplete="off" placeholder="例：zhangsan" />
              <small v-if="formErrors.username" class="pm-field-error">{{ formErrors.username }}</small>
            </label>
            <label>
              <span>姓名 <b>*</b></span>
              <input v-model="form.display_name" autocomplete="off" placeholder="例：张三" />
              <small v-if="formErrors.display_name" class="pm-field-error">{{ formErrors.display_name }}</small>
            </label>
            <label>
              <span>员工编号</span>
              <input v-model="form.employee_id" autocomplete="off" placeholder="选填" />
            </label>
            <label>
              <span>账号状态 <b>*</b></span>
              <select v-model="form.account_status" :disabled="form.username === 'admin'">
                <option value="active">启用</option>
                <option value="disabled">停用</option>
              </select>
              <small v-if="formErrors.account_status" class="pm-field-error">{{ formErrors.account_status }}</small>
            </label>
            <label>
              <span>部门编号 <b>*</b></span>
              <input v-model="form.department_id" autocomplete="off" placeholder="例：dept-market" />
              <small v-if="formErrors.department_id" class="pm-field-error">{{ formErrors.department_id }}</small>
            </label>
            <label>
              <span>部门名称 <b>*</b></span>
              <input v-model="form.department_name" autocomplete="off" placeholder="例：金融市场部" />
              <small v-if="formErrors.department_name" class="pm-field-error">{{ formErrors.department_name }}</small>
            </label>
            <label>
              <span>岗位</span>
              <input v-model="form.position" autocomplete="off" placeholder="选填" />
            </label>
            <label>
              <span>手机号</span>
              <input v-model="form.mobile" autocomplete="off" placeholder="选填" />
            </label>
            <label>
              <span>邮箱</span>
              <input v-model="form.email" type="email" autocomplete="off" placeholder="选填" />
              <small v-if="formErrors.email" class="pm-field-error">{{ formErrors.email }}</small>
            </label>
            <label class="pm-form-wide">
              <span>人员信息来源 <b>*</b></span>
              <input v-model="form.personnel_source" autocomplete="off" placeholder="由管理员填写，例：HR系统手工同步_2026-09" />
              <small v-if="formErrors.personnel_source" class="pm-field-error">{{ formErrors.personnel_source }}</small>
              <em>此字段用于数据追溯，保存时会随人员信息一起提交。</em>
            </label>
            <div v-if="editorError" class="pm-inline-error pm-form-wide" role="alert">{{ editorError }}</div>
            <footer class="pm-dialog-actions pm-form-wide">
              <button class="pm-btn pm-quiet" type="button" :disabled="savingUser" @click="closeEditor">取消</button>
              <button class="pm-btn pm-primary" type="submit" :disabled="savingUser">{{ savingUser ? '保存中…' : '保存' }}</button>
            </footer>
          </form>
        </section>
      </div>

      <div v-if="showImport" class="pm-dialog-backdrop" @click.self="closeImport">
        <section class="pm-dialog pm-import" role="dialog" aria-modal="true" aria-labelledby="personnel-import-title">
          <header class="pm-dialog-header">
            <div>
              <h3 id="personnel-import-title">Excel 批量导入</h3>
              <p>仅支持 .xlsx，系统会按账号新增或更新人员信息。</p>
            </div>
            <button class="pm-icon-btn" type="button" title="关闭" aria-label="关闭人员导入" @click="closeImport"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m18 6-12 12M6 6l12 12" /></svg></button>
          </header>

          <div class="pm-import-body">
            <section class="pm-format-card">
              <header>
                <div><strong>Excel 格式说明</strong><small>首行必须使用下列中文列名</small></div>
                <button class="pm-text-btn" type="button" :disabled="downloadingTemplate" @click="downloadTemplate">{{ downloadingTemplate ? '下载中…' : '下载标准模板' }}</button>
              </header>
              <div class="pm-column-group">
                <b>必填列</b>
                <span>账号</span><span>姓名</span><span>部门编号</span><span>部门名称</span>
              </div>
              <div class="pm-column-group optional">
                <b>选填列</b>
                <span>员工编号</span><span>邮箱</span><span>岗位</span><span>手机号</span><span>状态</span>
              </div>
              <ul id="personnel-import-format-help">
                <li>状态可填“启用 / 停用”或“active / disabled”，不填默认启用。</li>
                <li>Excel 不包含密码列；新增账号的默认密码为 <code>123456</code>。</li>
                <li>单个文件不超过 5 MB，最多 5000 个数据行。</li>
                <li>只允许上方列名；不能添加额外列，列名也不能重复。</li>
                <li>同一文件内账号不能重复；<code>admin</code> 账号不可通过导入覆盖。</li>
                <li>工作簿不能包含公式、隐藏行、隐藏列或隐藏工作表。</li>
              </ul>
            </section>

            <label class="pm-source-field">
              <span>本次人员信息来源 <b>*</b></span>
              <input v-model="importSource" placeholder="由管理员填写，例：HR导出_2026-09-08" />
              <small>该来源由前端统一提交，不从 Excel 读取；请勿在表格中增加“来源”列。</small>
            </label>

            <label class="pm-file-picker" :class="{ selected: importFile }">
              <input
                ref="importFileInput"
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                aria-label="选择人员 Excel 文件"
                aria-describedby="personnel-import-format-help personnel-import-file-help"
                @change="chooseImportFile"
              />
              <span class="pm-file-icon">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" /><path d="M14 2v6h6M8 13l2 3m0-3-2 3m5-3v3h3" />
                </svg>
              </span>
              <span v-if="importFile"><strong>{{ importFile.name }}</strong><small id="personnel-import-file-help">{{ formatBytes(importFile.size) }} · 点击或使用键盘更换文件</small></span>
              <span v-else><strong>选择 .xlsx 文件</strong><small id="personnel-import-file-help">请先核对列名和人员信息来源</small></span>
            </label>

            <div v-if="importError" class="pm-inline-error" role="alert">{{ importError }}</div>
            <div v-if="importResultText" class="pm-inline-success" role="status">{{ importResultText }}</div>
          </div>

          <footer class="pm-dialog-actions">
            <button class="pm-btn pm-quiet" type="button" :disabled="importing" @click="closeImport">{{ importResultText ? '完成' : '取消' }}</button>
            <button class="pm-btn pm-primary" type="button" :disabled="importing || !canImport || Boolean(importResultText)" @click="submitImport">{{ importing ? '导入中…' : '开始导入' }}</button>
          </footer>
        </section>
      </div>
    </section>
  </div>
</template>

<script>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { resetUserPassword } from '../../api/auth.js'
import {
  createPersonnelUser,
  getPersonnelImportTemplate,
  importPersonnelUsers,
  listPersonnelUsers,
  listTeamSpaceManagers,
  setTeamSpaceManager,
  updatePersonnelUser,
} from './api.js'
import {
  buildPersonnelPayload,
  emptyPersonnelForm,
  formatPersonnelValidationDetail,
  normalizePersonnelPage,
  summarizeImportResult,
  updateTeamManagerIds,
  validatePersonnelForm,
} from './model.js'

export default {
  name: 'PersonnelManagement',
  setup() {
    const users = ref([])
    const total = ref(0)
    const loading = ref(false)
    const keyword = ref('')
    const accountStatus = ref('')
    const departmentId = ref('')
    const page = ref(1)
    const pageSize = 50
    const noticeText = ref('')
    const noticeType = ref('success')
    const resettingUsername = ref('')
    const downloadingTemplate = ref(false)
    const teamManagerIds = ref(new Set())
    const teamPermissionUserId = ref('')

    const showEditor = ref(false)
    const editingUserId = ref('')
    const form = reactive(emptyPersonnelForm())
    const formErrors = ref({})
    const savingUser = ref(false)
    const editorError = ref('')

    const showImport = ref(false)
    const importSource = ref('')
    const importFile = ref(null)
    const importFileInput = ref(null)
    const importing = ref(false)
    const importError = ref('')
    const importResultText = ref('')

    let loadSequence = 0
    let filterTimer = null
    let noticeTimer = null

    const activeCount = computed(() => users.value.filter(user => user.account_status === 'active').length)
    const teamManagerCount = computed(() => teamManagerIds.value.size)
    const hasFilters = computed(() => Boolean(keyword.value.trim() || accountStatus.value || departmentId.value.trim()))
    const canImport = computed(() => Boolean(importFile.value && importSource.value.trim()))
    const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))
    const pageStart = computed(() => total.value ? (page.value - 1) * pageSize + 1 : 0)
    const pageEnd = computed(() => Math.min(total.value, page.value * pageSize))

    function resolveError(error, fallback) {
      const detail = error && error.response && error.response.data && error.response.data.detail
      if (typeof detail === 'string') return detail
      if (detail && typeof detail.message === 'string') {
        return formatPersonnelValidationDetail(detail) || detail.message
      }
      if (Array.isArray(detail)) {
        return detail.map(item => item && (item.message || item.msg)).filter(Boolean).join('；') || fallback
      }
      return (error && error.message) || fallback
    }

    function notify(text, type = 'success') {
      noticeText.value = text
      noticeType.value = type
      if (noticeTimer) clearTimeout(noticeTimer)
      noticeTimer = setTimeout(() => { noticeText.value = '' }, 5000)
    }

    async function loadUsers() {
      const sequence = ++loadSequence
      loading.value = true
      try {
        const payload = await listPersonnelUsers({
          keyword: keyword.value,
          accountStatus: accountStatus.value,
          departmentId: departmentId.value,
          page: page.value,
          pageSize,
        })
        if (sequence !== loadSequence) return
        const page = normalizePersonnelPage(payload)
        users.value = page.items
        total.value = page.total
      } catch (error) {
        if (sequence !== loadSequence) return
        notify(resolveError(error, '获取人员列表失败'), 'error')
      } finally {
        if (sequence === loadSequence) loading.value = false
      }
    }

    async function loadTeamManagers() {
      try {
        const payload = await listTeamSpaceManagers()
        teamManagerIds.value = new Set(
          (payload && Array.isArray(payload.items) ? payload.items : [])
            .map(item => String(item.user_id || ''))
            .filter(Boolean)
        )
      } catch (error) {
        notify(resolveError(error, '获取团队空间权限失败'), 'error')
      }
    }

    function isTeamManager(user) {
      return teamManagerIds.value.has(String(user.user_id || ''))
    }

    function canGrantTeamManager(user) {
      return user.account_status === 'active' && Boolean(String(user.department_id || '').trim())
    }

    function teamPermissionTitle(user) {
      if (teamPermissionUserId.value === user.user_id) return '正在保存权限'
      if (isTeamManager(user) && user.account_status === 'disabled') return '权限已暂停；可点击移除授权'
      if (!isTeamManager(user) && user.account_status === 'disabled') return '停用账号不能获得团队空间权限'
      if (!isTeamManager(user) && !String(user.department_id || '').trim()) return '账号缺少部门，不能获得团队空间权限'
      return isTeamManager(user)
        ? '点击取消其所属部门团队空间的创建与管理权限'
        : '点击授予其所属部门团队空间的创建与管理权限'
    }

    async function toggleTeamPermission(user) {
      const userId = String(user.user_id || '')
      if (!userId || teamPermissionUserId.value) return
      const enabled = !isTeamManager(user)
      teamPermissionUserId.value = userId
      try {
        await setTeamSpaceManager(userId, enabled)
        teamManagerIds.value = updateTeamManagerIds(teamManagerIds.value, userId, enabled)
        notify(enabled
          ? `已授权 ${user.display_name || user.username} 管理本部门团队空间`
          : `已取消 ${user.display_name || user.username} 的团队空间管理权限`)
      } catch (error) {
        notify(resolveError(error, '设置团队空间权限失败'), 'error')
      } finally {
        teamPermissionUserId.value = ''
      }
    }

    function scheduleLoad() {
      page.value = 1
      if (filterTimer) clearTimeout(filterTimer)
      filterTimer = setTimeout(loadUsers, 280)
    }

    function changePage(nextPage) {
      const normalized = Math.min(totalPages.value, Math.max(1, Number(nextPage) || 1))
      if (normalized === page.value) return
      page.value = normalized
      loadUsers()
    }

    function clearFilters() {
      keyword.value = ''
      accountStatus.value = ''
      departmentId.value = ''
    }

    function resetForm() {
      Object.assign(form, emptyPersonnelForm())
      formErrors.value = {}
      editorError.value = ''
    }

    function openCreate() {
      resetForm()
      editingUserId.value = ''
      showEditor.value = true
    }

    function openEdit(user) {
      resetForm()
      editingUserId.value = user.user_id
      Object.assign(form, {
        username: user.username,
        display_name: user.display_name,
        employee_id: user.employee_id,
        department_id: user.department_id,
        department_name: user.department_name,
        email: user.email,
        position: user.position,
        mobile: user.mobile,
        account_status: user.username === 'admin' ? 'active' : user.account_status,
        personnel_source: user.personnel_source,
      })
      showEditor.value = true
    }

    function closeEditor() {
      if (savingUser.value) return
      showEditor.value = false
    }

    async function saveUser() {
      const errors = validatePersonnelForm(form)
      formErrors.value = errors
      if (Object.keys(errors).length) return
      savingUser.value = true
      editorError.value = ''
      try {
        const payload = buildPersonnelPayload(form)
        const username = payload.username
        if (editingUserId.value) {
          delete payload.username
          await updatePersonnelUser(editingUserId.value, payload)
          notify(`已更新 ${payload.display_name}（${username}）`)
        } else {
          await createPersonnelUser(payload)
          notify(`已添加 ${payload.display_name}，初始密码为 123456`)
          page.value = 1
        }
        showEditor.value = false
        await loadUsers()
      } catch (error) {
        editorError.value = resolveError(error, editingUserId.value ? '更新人员失败' : '新增人员失败')
      } finally {
        savingUser.value = false
      }
    }

    async function resetPassword(user) {
      if (user.username === 'admin') {
        notify('系统管理员账号不可在人员管理中重置密码', 'error')
        return
      }
      if (!window.confirm(`确认将用户 ${user.username} 的密码重置为 123456？`)) return
      resettingUsername.value = user.username
      try {
        await resetUserPassword(user.username)
        notify(`用户 ${user.username} 的密码已重置为 123456`)
      } catch (error) {
        notify(resolveError(error, '密码重置失败'), 'error')
      } finally {
        resettingUsername.value = ''
      }
    }

    function openImport() {
      importSource.value = ''
      importFile.value = null
      importError.value = ''
      importResultText.value = ''
      if (importFileInput.value) importFileInput.value.value = ''
      showImport.value = true
    }

    function closeImport() {
      if (importing.value) return
      showImport.value = false
    }

    function chooseImportFile(event) {
      const file = event.target.files && event.target.files[0]
      importError.value = ''
      importResultText.value = ''
      importFile.value = null
      if (!file) return
      if (!file.name.toLowerCase().endsWith('.xlsx')) {
        importError.value = '仅支持 .xlsx 文件，请按页面说明使用标准模板。'
        event.target.value = ''
        return
      }
      if (file.size > 5 * 1024 * 1024) {
        importError.value = 'Excel 文件不能超过 5 MB。'
        event.target.value = ''
        return
      }
      importFile.value = file
    }

    async function submitImport() {
      if (!canImport.value || importing.value) return
      importing.value = true
      importError.value = ''
      importResultText.value = ''
      try {
        const result = await importPersonnelUsers(importFile.value, importSource.value.trim())
        importResultText.value = summarizeImportResult(result)
        notify(importResultText.value)
        page.value = 1
        await loadUsers()
      } catch (error) {
        importError.value = resolveError(error, '导入人员信息失败')
      } finally {
        importing.value = false
      }
    }

    async function downloadTemplate() {
      if (downloadingTemplate.value) return
      downloadingTemplate.value = true
      try {
        const result = await getPersonnelImportTemplate()
        const url = URL.createObjectURL(result.blob)
        const link = document.createElement('a')
        link.href = url
        link.download = result.filename
        document.body.appendChild(link)
        link.click()
        link.remove()
        URL.revokeObjectURL(url)
      } catch (error) {
        const message = resolveError(error, '下载导入模板失败')
        if (showImport.value) importError.value = message
        else notify(message, 'error')
      } finally {
        downloadingTemplate.value = false
      }
    }

    function statusLabel(status) {
      return status === 'disabled' ? '停用' : '启用'
    }

    function avatarText(user) {
      const value = user.display_name || user.username || '?'
      return value.slice(0, 1).toUpperCase()
    }

    function contactText(user) {
      return user.email || user.mobile || '未填写联系方式'
    }

    function formatBytes(value) {
      const bytes = Number(value || 0)
      if (bytes < 1024) return `${bytes} B`
      if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    }

    watch([keyword, accountStatus, departmentId], scheduleLoad)
    onMounted(() => {
      loadUsers()
      loadTeamManagers()
    })
    onBeforeUnmount(() => {
      if (filterTimer) clearTimeout(filterTimer)
      if (noticeTimer) clearTimeout(noticeTimer)
    })

    return {
      accountStatus,
      activeCount,
      avatarText,
      canImport,
      changePage,
      chooseImportFile,
      clearFilters,
      closeEditor,
      closeImport,
      contactText,
      departmentId,
      downloadTemplate,
      downloadingTemplate,
      editingUserId,
      editorError,
      form,
      formErrors,
      formatBytes,
      hasFilters,
      importError,
      importFile,
      importFileInput,
      importResultText,
      importSource,
      isTeamManager,
      importing,
      keyword,
      loadUsers,
      loading,
      noticeText,
      noticeType,
      openCreate,
      openEdit,
      openImport,
      page,
      pageEnd,
      pageStart,
      resetPassword,
      resettingUsername,
      saveUser,
      savingUser,
      showEditor,
      showImport,
      statusLabel,
      submitImport,
      teamManagerCount,
      teamPermissionTitle,
      teamPermissionUserId,
      toggleTeamPermission,
      canGrantTeamManager,
      total,
      totalPages,
      users,
    }
  },
}
</script>

<style scoped>
.pm-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.52);
  backdrop-filter: blur(2px);
}

.pm-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  width: min(1220px, 96vw);
  height: min(860px, 92vh);
  overflow: hidden;
  color: var(--text-primary);
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 18px;
  box-shadow: 0 26px 70px rgba(15, 23, 42, 0.28);
}

.pm-header,
.pm-dialog-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  border-bottom: 1px solid var(--border-color);
}

.pm-header { padding: 20px 24px; }

.pm-heading { display: flex; align-items: center; gap: 13px; }
.pm-heading-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  color: #fff;
  background: linear-gradient(145deg, #38bdf8, #0284c7);
  border-radius: 12px;
  box-shadow: 0 8px 20px rgba(14, 165, 233, 0.2);
}
.pm-heading-icon svg { width: 23px; height: 23px; }
.pm-heading h2, .pm-dialog-header h3 { margin: 0; color: var(--text-primary); font-weight: 650; }
.pm-heading h2 { font-size: 19px; }
.pm-heading p, .pm-dialog-header p { margin: 4px 0 0; color: var(--text-secondary); font-size: 12px; }

.pm-icon-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 34px;
  height: 34px;
  color: var(--text-secondary);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 9px;
  cursor: pointer;
}
.pm-icon-btn:hover { color: var(--text-primary); background: var(--bg-tertiary); border-color: var(--border-color); }
.pm-icon-btn svg { width: 19px; height: 19px; }

.pm-toolbar {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 14px 24px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
}
.pm-toolbar-spacer { flex: 1 1 auto; }
.pm-search, .pm-filter {
  display: flex;
  align-items: center;
  height: 38px;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 9px;
}
.pm-search { width: min(310px, 27vw); padding: 0 11px; gap: 8px; }
.pm-search:focus-within, .pm-filter:focus-within { border-color: var(--accent-color, #0ea5e9); box-shadow: 0 0 0 3px rgba(14, 165, 233, 0.1); }
.pm-search svg { width: 17px; height: 17px; flex: 0 0 auto; }
.pm-search input, .pm-filter input, .pm-filter select {
  min-width: 0;
  width: 100%;
  height: 100%;
  color: var(--text-primary);
  background: transparent;
  border: 0;
  outline: 0;
  font-size: 12px;
}
.pm-search input::placeholder, .pm-filter input::placeholder, .pm-form input::placeholder, .pm-source-field input::placeholder { color: var(--text-secondary); opacity: .72; }
.pm-filter { padding-left: 10px; }
.pm-filter > span { white-space: nowrap; padding-right: 8px; font-size: 11px; border-right: 1px solid var(--border-color); }
.pm-filter select { width: 82px; padding: 0 8px; cursor: pointer; }
.pm-department-filter input { width: 94px; padding: 0 9px; }

.pm-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 13px;
  white-space: nowrap;
  color: var(--text-primary);
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 9px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: transform .15s ease, border-color .15s ease, background .15s ease;
}
.pm-btn:hover:not(:disabled) { transform: translateY(-1px); border-color: var(--accent-color, #0ea5e9); }
.pm-btn:disabled, .pm-text-btn:disabled, .pm-row-actions button:disabled { opacity: .55; cursor: not-allowed; }
.pm-btn svg { width: 16px; height: 16px; }
.pm-btn.pm-primary { color: #fff; background: var(--accent-color, #0ea5e9); border-color: var(--accent-color, #0ea5e9); box-shadow: 0 5px 14px rgba(14, 165, 233, .18); }
.pm-btn.pm-primary:hover:not(:disabled) { filter: brightness(.97); }
.pm-btn.pm-quiet { background: var(--bg-primary); }

.pm-team-access-guide {
  display: flex;
  align-items: flex-start;
  gap: 11px;
  margin: 14px 24px 0;
  padding: 12px 14px;
  color: var(--text-primary);
  background: color-mix(in srgb, var(--accent-color, #0ea5e9) 7%, var(--bg-primary));
  border: 1px solid color-mix(in srgb, var(--accent-color, #0ea5e9) 24%, var(--border-color));
  border-radius: 11px;
}
.pm-team-access-guide > span {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  color: var(--accent-color, #0ea5e9);
  background: var(--bg-secondary);
  border-radius: 9px;
}
.pm-team-access-guide svg { width: 18px; height: 18px; }
.pm-team-access-guide div { min-width: 0; }
.pm-team-access-guide strong { display: block; font-size: 12px; }
.pm-team-access-guide p { margin: 3px 0 0; color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.pm-team-access-guide small { display: block; margin-top: 3px; color: var(--text-secondary); font-size: 10px; line-height: 1.5; opacity: .86; }

.pm-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 12px 24px 0;
  padding: 9px 11px;
  border: 1px solid;
  border-radius: 9px;
  font-size: 12px;
}
.pm-notice.success { color: #047857; background: rgba(16, 185, 129, .08); border-color: rgba(16, 185, 129, .25); }
.pm-notice.error { color: #dc2626; background: rgba(239, 68, 68, .08); border-color: rgba(239, 68, 68, .24); }
.pm-notice svg { width: 16px; height: 16px; }
.pm-notice span { flex: 1; }
.pm-notice button { padding: 0 3px; color: currentColor; background: transparent; border: 0; font-size: 18px; cursor: pointer; }

.pm-summary {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 55px;
  margin: 14px 24px 0;
  padding: 9px 15px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 11px;
}
.pm-summary > div { display: flex; align-items: baseline; gap: 7px; }
.pm-summary strong { color: var(--text-primary); font-size: 19px; line-height: 1; }
.pm-summary span { color: var(--text-secondary); font-size: 11px; }
.pm-summary > i { width: 1px; height: 22px; background: var(--border-color); }
.pm-summary .pm-summary-tip { display: flex; align-items: center; gap: 6px; margin-left: auto; }
.pm-summary-tip svg { width: 15px; height: 15px; color: #10b981; }

.pm-content { flex: 1 1 auto; min-height: 0; padding: 14px 24px 0; }
.pm-table-wrap { height: 100%; overflow: auto; background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 12px; }
.pm-table { width: 100%; min-width: 1160px; border-collapse: separate; border-spacing: 0; table-layout: fixed; }
.pm-table th { position: sticky; top: 0; z-index: 1; padding: 11px 13px; color: var(--text-secondary); background: var(--bg-tertiary); border-bottom: 1px solid var(--border-color); text-align: left; font-size: 11px; font-weight: 650; }
.pm-table th:nth-child(1) { width: 190px; }
.pm-table th:nth-child(2) { width: 100px; }
.pm-table th:nth-child(3) { width: 165px; }
.pm-table th:nth-child(4) { width: 185px; }
.pm-table th:nth-child(5) { width: 155px; }
.pm-table th:nth-child(6) { width: 145px; }
.pm-table th:nth-child(7) { width: 82px; }
.pm-table th:nth-child(8) { width: 155px; }
.pm-table td { height: 64px; padding: 9px 13px; color: var(--text-primary); border-bottom: 1px solid var(--border-color); vertical-align: middle; font-size: 12px; }
.pm-table tbody tr:last-child td { border-bottom: 0; }
.pm-table tbody tr:hover td { background: color-mix(in srgb, var(--accent-color, #0ea5e9) 3%, transparent); }
.pm-actions-head { text-align: right !important; }
.pm-person { display: flex; align-items: center; gap: 10px; min-width: 0; }
.pm-person > span { display: inline-flex; align-items: center; justify-content: center; flex: 0 0 auto; width: 34px; height: 34px; color: #0369a1; background: rgba(14, 165, 233, .12); border-radius: 10px; font-size: 13px; font-weight: 700; }
.pm-person div, .pm-stacked { display: flex; flex-direction: column; min-width: 0; }
.pm-person strong, .pm-stacked strong, .pm-person small, .pm-stacked small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pm-person strong, .pm-stacked strong { color: var(--text-primary); font-weight: 600; }
.pm-person small, .pm-stacked small { margin-top: 2px; color: var(--text-secondary); font-size: 10px; }
.pm-cell-main { overflow-wrap: anywhere; }
.pm-source { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
.pm-status { display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: 999px; font-size: 10px; font-weight: 600; }
.pm-status i { width: 6px; height: 6px; border-radius: 50%; }
.pm-status.active { color: #047857; background: rgba(16, 185, 129, .1); }
.pm-status.active i { background: #10b981; }
.pm-status.disabled { color: #64748b; background: rgba(100, 116, 139, .12); }
.pm-status.disabled i { background: #94a3b8; }
.pm-access-note { display: inline-flex; align-items: center; min-height: 25px; padding: 0 8px; color: var(--text-secondary); background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 999px; font-size: 10px; font-weight: 650; }
.pm-access-note.global { color: #0369a1; background: rgba(14, 165, 233, .1); border-color: rgba(14, 165, 233, .2); }
.pm-permission-switch { display: inline-flex; align-items: center; gap: 7px; padding: 3px 0; color: var(--text-secondary); background: transparent; border: 0; cursor: pointer; }
.pm-permission-switch:disabled { opacity: .5; cursor: not-allowed; }
.pm-permission-switch > i { position: relative; display: inline-block; flex: 0 0 auto; width: 29px; height: 17px; background: #cbd5e1; border-radius: 999px; transition: background .18s ease; }
.pm-permission-switch > i > span { position: absolute; top: 2px; left: 2px; width: 13px; height: 13px; background: #fff; border-radius: 50%; box-shadow: 0 1px 3px rgba(15, 23, 42, .25); transition: transform .18s ease; }
.pm-permission-switch > strong { white-space: nowrap; font-size: 10px; font-weight: 650; }
.pm-permission-switch.active { color: #047857; }
.pm-permission-switch.active > i { background: #10b981; }
.pm-permission-switch.active > i > span { transform: translateX(12px); }
.pm-permission-switch.pending > i > span { animation: pm-switch-pulse .7s ease-in-out infinite alternate; }
@keyframes pm-switch-pulse { to { opacity: .45; } }
.pm-row-actions { display: flex; align-items: center; justify-content: flex-end; gap: 5px; }
.pm-row-actions button { padding: 5px 7px; white-space: nowrap; color: var(--accent-color, #0284c7); background: transparent; border: 0; border-radius: 6px; font-size: 10px; cursor: pointer; }
.pm-row-actions button:hover:not(:disabled) { background: rgba(14, 165, 233, .1); }

.pm-loading, .pm-empty { display: flex; align-items: center; justify-content: center; color: var(--text-secondary); }
.pm-loading { min-height: 220px; gap: 9px; }
.pm-loading span { width: 18px; height: 18px; border: 2px solid var(--border-color); border-top-color: var(--accent-color, #0ea5e9); border-radius: 50%; animation: pm-spin .8s linear infinite; }
.pm-empty { min-height: 260px; flex-direction: column; }
.pm-empty > span { display: inline-flex; align-items: center; justify-content: center; width: 48px; height: 48px; color: var(--accent-color, #0ea5e9); background: rgba(14, 165, 233, .1); border-radius: 15px; }
.pm-empty svg { width: 26px; height: 26px; }
.pm-empty strong { margin-top: 12px; color: var(--text-primary); font-size: 14px; }
.pm-empty p { margin: 4px 0 8px; font-size: 11px; }
@keyframes pm-spin { to { transform: rotate(360deg); } }

.pm-text-btn { padding: 2px 3px; color: var(--accent-color, #0284c7); background: transparent; border: 0; font-size: 11px; font-weight: 600; cursor: pointer; }
.pm-footer { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 24px 14px; color: var(--text-secondary); font-size: 10px; }
.pm-pagination { display: flex; align-items: center; justify-content: flex-end; gap: 10px; }
.pm-pagination strong { min-width: 48px; text-align: center; color: var(--text-primary); font-size: 11px; }

.pm-dialog-backdrop { position: absolute; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(15, 23, 42, .48); backdrop-filter: blur(1px); }
.pm-dialog { display: flex; flex-direction: column; width: min(760px, 94%); max-height: 92%; overflow: hidden; color: var(--text-primary); background: var(--bg-primary); border: 1px solid var(--border-color); border-radius: 16px; box-shadow: 0 24px 60px rgba(15, 23, 42, .28); }
.pm-dialog-header { padding: 18px 20px; }
.pm-dialog-header h3 { font-size: 16px; }
.pm-form, .pm-import-body { min-height: 0; overflow-y: auto; padding: 18px 20px; }
.pm-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 15px 16px; }
.pm-form label, .pm-source-field { display: flex; flex-direction: column; gap: 6px; }
.pm-form label > span, .pm-source-field > span { color: var(--text-primary); font-size: 11px; font-weight: 600; }
.pm-form label > span b, .pm-source-field > span b { color: #ef4444; }
.pm-form input, .pm-form select, .pm-source-field input { width: 100%; height: 38px; padding: 0 11px; color: var(--text-primary); background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 8px; outline: none; font-size: 12px; }
.pm-form input:focus, .pm-form select:focus, .pm-source-field input:focus { border-color: var(--accent-color, #0ea5e9); box-shadow: 0 0 0 3px rgba(14, 165, 233, .1); }
.pm-form input:disabled, .pm-form select:disabled { opacity: .68; cursor: not-allowed; }
.pm-form label em { color: var(--text-secondary); font-size: 10px; font-style: normal; }
.pm-form-wide { grid-column: 1 / -1; }
.pm-field-error { color: #dc2626; font-size: 10px; }
.pm-dialog-actions { display: flex; align-items: center; justify-content: flex-end; gap: 9px; padding: 14px 20px; border-top: 1px solid var(--border-color); }
.pm-form .pm-dialog-actions { margin: 3px -20px -18px; }

.pm-import { width: min(720px, 94%); }
.pm-import-body { display: flex; flex-direction: column; gap: 15px; }
.pm-format-card { padding: 14px; background: var(--bg-secondary); border: 1px solid var(--border-color); border-radius: 11px; }
.pm-format-card header { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; }
.pm-format-card header div { display: flex; flex-direction: column; }
.pm-format-card header strong { font-size: 12px; }
.pm-format-card header small { margin-top: 2px; color: var(--text-secondary); font-size: 10px; }
.pm-column-group { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; }
.pm-column-group b { width: 48px; color: #dc2626; font-size: 10px; }
.pm-column-group span { padding: 3px 7px; color: #0369a1; background: rgba(14, 165, 233, .1); border: 1px solid rgba(14, 165, 233, .15); border-radius: 6px; font-size: 10px; }
.pm-column-group.optional b { color: var(--text-secondary); }
.pm-column-group.optional span { color: var(--text-secondary); background: var(--bg-primary); border-color: var(--border-color); }
.pm-format-card ul { margin: 12px 0 0 17px; color: var(--text-secondary); font-size: 10px; line-height: 1.8; }
.pm-format-card code { padding: 1px 4px; color: var(--text-primary); background: var(--bg-primary); border-radius: 4px; }
.pm-source-field small { color: var(--text-secondary); font-size: 10px; }
.pm-file-picker { display: flex; align-items: center; gap: 12px; padding: 14px; background: var(--bg-secondary); border: 1px dashed #94a3b8; border-radius: 11px; cursor: pointer; }
.pm-file-picker:hover, .pm-file-picker.selected, .pm-file-picker:focus-within { border-color: var(--accent-color, #0ea5e9); background: rgba(14, 165, 233, .05); }
.pm-file-picker:focus-within { box-shadow: 0 0 0 3px rgba(14, 165, 233, .12); }
.pm-file-picker > input {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
.pm-file-picker .pm-file-icon { display: inline-flex; align-items: center; justify-content: center; width: 40px; height: 40px; color: #0f9f6e; background: rgba(16, 185, 129, .1); border-radius: 10px; }
.pm-file-picker svg { width: 23px; height: 23px; }
.pm-file-picker > span:last-child { display: flex; flex-direction: column; min-width: 0; }
.pm-file-picker strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-primary); font-size: 12px; }
.pm-file-picker small { margin-top: 2px; color: var(--text-secondary); font-size: 10px; }
.pm-inline-error, .pm-inline-success { padding: 9px 11px; border-radius: 8px; font-size: 11px; }
.pm-inline-error { color: #dc2626; background: rgba(239, 68, 68, .08); border: 1px solid rgba(239, 68, 68, .2); }
.pm-inline-success { color: #047857; background: rgba(16, 185, 129, .08); border: 1px solid rgba(16, 185, 129, .2); }

@media (max-width: 980px) {
  .pm-overlay { padding: 10px; }
  .pm-panel { width: 100%; height: 96vh; }
  .pm-toolbar { flex-wrap: wrap; }
  .pm-search { width: 100%; }
  .pm-toolbar-spacer { display: none; }
  .pm-summary-tip { display: none !important; }
}

@media (max-width: 620px) {
  .pm-heading p, .pm-footer span { display: none; }
  .pm-header, .pm-toolbar { padding-left: 14px; padding-right: 14px; }
  .pm-team-access-guide { margin-left: 14px; margin-right: 14px; }
  .pm-summary { margin-left: 14px; margin-right: 14px; }
  .pm-content { padding-left: 14px; padding-right: 14px; }
  .pm-form { grid-template-columns: 1fr; }
  .pm-form-wide { grid-column: 1; }
  .pm-dialog-backdrop { padding: 8px; }
}
</style>
