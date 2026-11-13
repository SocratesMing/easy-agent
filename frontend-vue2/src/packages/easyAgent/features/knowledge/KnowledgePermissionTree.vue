<template>
  <div class="ke-perm-tree">
    <section v-for="dept in tree" :key="dept.key" class="ke-perm-branch">
      <div class="ke-perm-row level-dept">
        <button type="button" class="ke-perm-caret" title="展开/收起" @click="$emit('toggle-expand', dept.key)"><IconChevronDown :class="{ rotated: isCollapsed(dept.key) }" /></button>
        <input type="checkbox" v-indeterminate="indeterminate(dept)" :checked="checked(dept)" @change="$emit('toggle-node', dept, $event.target.checked)" />
        <span class="ke-perm-name">{{ dept.name }}</span>
        <small class="ke-perm-count">{{ selectedCount(dept) }}/{{ dept.allUsers.length }}</small>
      </div>
      <div v-show="!isCollapsed(dept.key)" class="ke-perm-children">
        <label v-for="user in dept.users" :key="user.user_id" class="ke-perm-user">
          <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
          <span>{{ user.display_name }}</span>
          <small>{{ user.username }}</small>
        </label>
        <section v-for="division in dept.divisions" :key="division.key" class="ke-perm-branch">
          <div class="ke-perm-row level-division">
            <button type="button" class="ke-perm-caret" title="展开/收起" @click="$emit('toggle-expand', division.key)"><IconChevronDown :class="{ rotated: isCollapsed(division.key) }" /></button>
            <input type="checkbox" v-indeterminate="indeterminate(division)" :checked="checked(division)" @change="$emit('toggle-node', division, $event.target.checked)" />
            <span class="ke-perm-name">{{ division.name }}</span>
            <small class="ke-perm-count">{{ selectedCount(division) }}/{{ division.allUsers.length }}</small>
          </div>
          <div v-show="!isCollapsed(division.key)" class="ke-perm-children">
            <label v-for="user in division.users" :key="user.user_id" class="ke-perm-user">
              <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
              <span>{{ user.display_name }}</span>
              <small>{{ user.username }}</small>
            </label>
            <section v-for="team in division.teams" :key="team.key" class="ke-perm-branch">
              <div class="ke-perm-row level-team">
                <button type="button" class="ke-perm-caret" title="展开/收起" @click="$emit('toggle-expand', team.key)"><IconChevronDown :class="{ rotated: isCollapsed(team.key) }" /></button>
                <input type="checkbox" v-indeterminate="indeterminate(team)" :checked="checked(team)" @change="$emit('toggle-node', team, $event.target.checked)" />
                <span class="ke-perm-name">{{ team.name }}</span>
                <small class="ke-perm-count">{{ selectedCount(team) }}/{{ team.allUsers.length }}</small>
              </div>
              <div v-show="!isCollapsed(team.key)" class="ke-perm-children">
                <label v-for="user in team.users" :key="user.user_id" class="ke-perm-user">
                  <input type="checkbox" :checked="user.checked" @change="user.checked = $event.target.checked" />
                  <span>{{ user.display_name }}</span>
                  <small>{{ user.username }}</small>
                </label>
              </div>
            </section>
          </div>
        </section>
      </div>
    </section>
    <p v-if="!tree.length" class="ke-perm-empty">{{ emptyText }}</p>
  </div>
</template>

<script>
import { IconChevronDown } from './icons.js'

export default {
  components: { IconChevronDown },
  props: {
    tree: { type: Array, default: () => [] },
    // collapsed 只作为渲染依赖：父级替换对象后本组件重渲染，具体判定交给 isCollapsed
    collapsed: { type: Object, default: () => ({}) },
    isCollapsed: { type: Function, required: true },
    emptyText: { type: String, default: '' },
  },
  emits: ['toggle-expand', 'toggle-node'],
  // Vue 2 模板无法直接绑定 checkbox 的 indeterminate 属性，用指令补齐
  directives: {
    indeterminate: {
      bind(el, binding) { el.indeterminate = Boolean(binding.value) },
      update(el, binding) { el.indeterminate = Boolean(binding.value) },
    },
  },
  methods: {
    selectedCount(node) { return node.allUsers.reduce((count, user) => count + (user.checked ? 1 : 0), 0) },
    checked(node) { return node.allUsers.length > 0 && node.allUsers.every(user => user.checked) },
    indeterminate(node) {
      const selected = this.selectedCount(node)
      return selected > 0 && selected < node.allUsers.length
    },
  },
}
</script>

<style scoped>
.ke-perm-tree{margin-top:7px;border:1px solid #e3e7e4;border-radius:10px;max-height:340px;overflow:auto;padding:6px 10px;background:#fff}
.ke-perm-branch+.ke-perm-branch{margin-top:4px}
.ke-perm-row{display:grid;grid-template-columns:24px 18px 1fr auto;gap:7px;align-items:center;padding:7px 4px;border-radius:8px}
.ke-perm-row:hover{background:#f7faf8}
.ke-perm-row.level-dept>.ke-perm-name{font-weight:650;font-size:12px;color:#3d4640}
.ke-perm-row.level-division>.ke-perm-name{font-weight:600;font-size:11px;color:#4a544d;padding-left:2px}
.ke-perm-row.level-team>.ke-perm-name{font-weight:500;font-size:11px;color:#59615c;padding-left:2px}
.ke-perm-name{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ke-perm-count{color:#9aa19c;font-size:9px}
.ke-perm-caret{width:22px;height:22px;padding:0;border:0;background:transparent;border-radius:6px;display:grid;place-items:center;color:#9aa19c;cursor:pointer}
.ke-perm-caret:hover{background:#eef3ef;color:#59615c}
.ke-perm-caret svg{width:13px;height:13px;transition:transform .15s ease}
.ke-perm-caret svg.rotated{transform:rotate(-90deg)}
.ke-perm-children{margin-left:24px;border-left:1px dashed #dfe4e0;padding-left:8px}
.ke-perm-user{display:grid;grid-template-columns:18px 1fr auto;gap:8px;align-items:center;padding:5px 4px;border-radius:8px;cursor:pointer;color:#3d4640;font-size:11px}
.ke-perm-user:hover{background:#f7faf8}
.ke-perm-user span{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ke-perm-user small{color:#9aa19c;font-size:9px}
.ke-perm-empty{margin:0;padding:22px 10px;color:#9aa19c;text-align:center;font-size:10px}
.ke-perm-tree input[type=checkbox]{width:15px;height:15px;flex:none;box-sizing:border-box;margin:0;padding:0;border:0;background:transparent;accent-color:var(--ke-accent);cursor:pointer}
</style>
