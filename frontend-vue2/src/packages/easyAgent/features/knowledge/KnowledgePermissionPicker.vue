<template>
  <div class="ke-perm-picker">
    <div class="ke-perm-filters">
      <label class="ke-perm-filter">
        <span>部门</span>
        <select v-model="filters.department" @change="onDepartmentChange">
          <option value="">全部部门</option>
          <option v-for="option in departmentOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label class="ke-perm-filter">
        <span>处室</span>
        <select v-model="filters.division" @change="onDivisionChange">
          <option value="">全部处室</option>
          <option v-for="option in divisionOptions" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <label class="ke-perm-filter">
        <span>团队</span>
        <select v-model="filters.team" @change="onTeamChange">
          <option value="">全部团队</option>
          <option v-for="option in teamOptions" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <label class="ke-perm-filter">
        <span>系统</span>
        <select v-model="filters.system">
          <option value="">全部系统</option>
          <option v-for="option in systemOptions" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
      <label class="ke-perm-filter">
        <span>角色</span>
        <select v-model="filters.role">
          <option value="">全部角色</option>
          <option v-for="option in roleOptions" :key="option" :value="option">{{ option }}</option>
        </select>
      </label>
    </div>
    <label class="ke-perm-search">
      <IconSearch />
      <input v-model="query" placeholder="搜索人员姓名或账号" />
      <button v-if="query" type="button" aria-label="清空搜索" @click="query = ''"><IconX /></button>
    </label>
    <div class="ke-perm-list">
      <div class="ke-perm-list-head">
        <label class="ke-perm-select-all">
          <input type="checkbox" :checked="allChecked" :indeterminate.prop="someChecked" @change="toggleAll($event.target.checked)" />
          <span>全选当前筛选</span>
        </label>
        <small>{{ filteredUsers.length }} 人</small>
      </div>
      <label v-for="user in filteredUsers" :key="user.user_id" class="ke-perm-user">
        <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
        <span>{{ user.display_name }}</span>
        <small>{{ user.username }}</small>
      </label>
      <p v-if="!filteredUsers.length" class="ke-perm-empty">{{ listEmptyText }}</p>
    </div>
  </div>
</template>

<script>
import { IconSearch, IconX } from './icons.js'
import { filterPermissionUsers } from './utils/permissions.js'

export default {
  name: 'KnowledgePermissionPicker',
  components: { IconSearch, IconX },
  props: {
    // 人员目录对象（含 checked 勾选态）；勾选直接写回对象，保存时由父级汇总
    users: { type: Array, default: () => [] },
    emptyText: { type: String, default: '暂无可选人员。' },
  },
  data() {
    return {
      query: '',
      filters: { department: '', division: '', team: '', role: '', system: '' },
    }
  },
  computed: {
    departmentScope() {
      if (!this.filters.department) return this.users
      return this.users.filter(user => this.departmentKey(user) === this.filters.department)
    },
    divisionScope() {
      if (!this.filters.division) return this.departmentScope
      return this.departmentScope.filter(user => this.text(user.division_name) === this.filters.division)
    },
    teamScope() {
      if (!this.filters.team) return this.divisionScope
      return this.divisionScope.filter(user => this.text(user.team_name) === this.filters.team)
    },
    filteredUsers() {
      const base = this.query.trim()
        ? filterPermissionUsers(this.users, this.query)
        : this.users
      const { department, division, team, role, system } = this.filters
      return base.filter(user => (
        (!department || this.departmentKey(user) === department)
        && (!division || this.text(user.division_name) === division)
        && (!team || this.text(user.team_name) === team)
        && (!role || this.rolesOf(user).includes(role))
        && (!system || this.systemsOf(user).includes(system))
      ))
    },
    departmentOptions() {
      const options = new Map()
      for (const user of this.users) {
        const value = this.departmentKey(user)
        if (!options.has(value)) options.set(value, this.departmentLabel(user))
      }
      return [...options.entries()]
        .map(([value, label]) => ({ value, label }))
        .sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
    },
    divisionOptions() {
      return this.uniqueValues(this.departmentScope.map(user => user.division_name))
    },
    teamOptions() {
      return this.uniqueValues(this.divisionScope.map(user => user.team_name))
    },
    roleOptions() {
      const roles = this.teamScope.flatMap(user => this.rolesOf(user))
      return this.uniqueValues(roles)
    },
    systemOptions() {
      const systems = this.teamScope.flatMap(user => this.systemsOf(user))
      return this.uniqueValues(systems)
    },
    allChecked() {
      return this.filteredUsers.length > 0 && this.filteredUsers.every(user => user.checked)
    },
    someChecked() {
      const checked = this.filteredUsers.filter(user => user.checked).length
      return checked > 0 && checked < this.filteredUsers.length
    },
    listEmptyText() {
      return this.users.length ? '没有符合筛选条件的人员' : this.emptyText
    },
  },
  methods: {
    text(value) {
      return String(value == null ? '' : value).trim()
    },
    rolesOf(user) {
      return this.text(user.position)
        .split(/[/、,，;；|｜]/)
        .map(value => value.trim())
        .filter(Boolean)
    },
    systemsOf(user) {
      return this.text(user.systems)
        .split(/[/、,，;；|｜]/)
        .map(value => value.trim())
        .filter(Boolean)
    },
    departmentKey(user) {
      return this.text(user.department_id) || `name:${this.text(user.department_name)}`
    },
    departmentLabel(user) {
      return this.text(user.department_name) || this.text(user.department_id) || '未分配部门'
    },
    uniqueValues(values) {
      return [...new Set(values.map(value => this.text(value)).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, 'zh-CN'))
    },
    onDepartmentChange() {
      // 上级筛选变化后，下级已选值可能不再存在：逐级清空依赖项
      this.filters.division = ''
      this.filters.team = ''
      this.filters.role = ''
      this.filters.system = ''
    },
    onDivisionChange() {
      this.filters.team = ''
      this.filters.role = ''
      this.filters.system = ''
    },
    onTeamChange() {
      this.filters.role = ''
      this.filters.system = ''
    },
    toggleAll(checked) {
      this.filteredUsers.forEach(user => { user.checked = checked })
    },
  },
}
</script>

<style scoped>
.ke-perm-filters{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px;margin-top:10px}
.ke-perm-filter{display:flex;flex-direction:column;gap:5px;color:var(--text-secondary);font-size:10px}
.ke-perm-filter select{width:100%;box-sizing:border-box;height:32px;border:1px solid var(--border-color);border-radius:9px;background:var(--bg-secondary);color:var(--text-primary);padding:0 7px;font:inherit;font-size:11px;outline:0}
.ke-perm-filter select:focus{border-color:var(--accent-color);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent-color) 14%,transparent)}
.ke-perm-search{height:34px;margin-top:8px;box-sizing:border-box;border:1px solid var(--border-color);background:var(--bg-tertiary);border-radius:9px;display:flex;align-items:center;gap:7px;padding:0 9px;color:var(--text-secondary)}
.ke-perm-search:focus-within{border-color:var(--accent-color);background:var(--bg-secondary);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent-color) 14%,transparent)}
.ke-perm-search input{min-width:0;flex:1;border:0;outline:0;background:transparent;color:var(--text-primary);font:inherit;font-size:12px;padding:0}
.ke-perm-search button{border:0;background:transparent;color:var(--text-secondary);display:grid;place-items:center;padding:0;cursor:pointer}
.ke-perm-search svg{width:15px;height:15px;flex:none}
.ke-perm-list{margin-top:8px;border:1px solid var(--border-color);border-radius:10px;max-height:320px;overflow:auto;padding:6px 10px;background:var(--bg-secondary)}
.ke-perm-list-head{position:sticky;top:0;z-index:1;display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 4px;background:var(--bg-secondary);border-bottom:1px solid var(--border-color);color:var(--text-secondary);font-size:10px}
.ke-perm-select-all{display:inline-flex;align-items:center;gap:6px;cursor:pointer;color:var(--text-secondary);font-size:10px}
.ke-perm-user{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:8px;align-items:center;padding:5px 4px;border-radius:8px;cursor:pointer;color:var(--text-primary);font-size:11px}
.ke-perm-user:hover{background:var(--bg-tertiary)}
.ke-perm-user span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ke-perm-user small{color:var(--text-secondary);font-size:9px}
.ke-perm-empty{margin:0;padding:22px 10px;color:var(--text-secondary);text-align:center;font-size:10px}
.ke-perm-list input[type=checkbox]{width:15px;height:15px;flex:none;box-sizing:border-box;margin:0;padding:0;border:0;background:transparent;accent-color:var(--accent-color);cursor:pointer}
@media (max-width:560px){.ke-perm-filters{grid-template-columns:repeat(2,minmax(0,1fr))}}
</style>
