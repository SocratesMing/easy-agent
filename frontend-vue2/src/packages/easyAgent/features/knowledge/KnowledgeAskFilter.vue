<template>
  <div class="ke-ask-filter">
    <label>发布日期
      <input v-model="dateFrom" type="date" :max="dateTo || null" @change="emitFilter" />
      <span>至</span>
      <input v-model="dateTo" type="date" :min="dateFrom || null" @change="emitFilter" />
    </label>
    <label>标签<input v-model="tag" type="text" placeholder="不限" @input="emitFilter" /></label>
    <label>来源<input v-model="source" type="text" placeholder="不限" @input="emitFilter" /></label>
    <button v-if="active" type="button" class="ke-filter-clear" @click="clear">清除</button>
  </div>
</template>

<script>
/**
 * 知识库问答元数据过滤器：日期区间（闭区间）+ 单标签 + 来源。
 * 通过 change 事件把后端所需的 metadata_filter 对象交给父组件；
 * 未设置任何条件时传 null，表示全范围检索。
 */
export default {
  name: 'KnowledgeAskFilter',
  data() {
    return { dateFrom: '', dateTo: '', tag: '', source: '' }
  },
  computed: {
    active() {
      return Boolean(
        this.dateFrom || this.dateTo || this.tag.trim() || this.source.trim(),
      )
    },
  },
  methods: {
    emitFilter() {
      if (!this.active) {
        this.$emit('change', null)
        return
      }
      const filter = {}
      if (this.dateFrom) filter.publish_date_from = this.dateFrom
      if (this.dateTo) filter.publish_date_to = this.dateTo
      if (this.tag.trim()) filter.report_tags = [this.tag.trim()]
      if (this.source.trim()) filter.report_second_sources = [this.source.trim()]
      this.$emit('change', filter)
    },
    clear() {
      this.dateFrom = ''
      this.dateTo = ''
      this.tag = ''
      this.source = ''
      this.$emit('change', null)
    },
  },
}
</script>

<style scoped>
.ke-ask-filter{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0 0 6px;font-size:11px;color:var(--text-secondary)}
.ke-ask-filter label{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
.ke-ask-filter input[type=date],.ke-ask-filter input[type=text]{height:24px;border:1px solid #dbe3dd;border-radius:6px;padding:0 6px;background:var(--bg-secondary);color:var(--text-primary);font-size:11px}
.ke-ask-filter input[type=text]{width:72px}
.ke-filter-clear{border:0;background:transparent;color:var(--accent-color,#2f7d5b);cursor:pointer;font-size:11px;padding:0 2px}
</style>
