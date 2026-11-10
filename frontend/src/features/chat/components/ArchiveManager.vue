<template><div class="archive-manager" :class="{ 'is-open': open }">
  <section v-if="open" class="archive-panel">
    <header class="archive-header"><h3>归档管理</h3><p>查看和管理已归档的会话，可取消归档或永久删除</p></header>
    <div class="archive-search">
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7" /><path d="m16 16 4 4" /></svg>
      <input ref="search" v-model="query" placeholder="搜索归档会话" aria-label="搜索归档会话" />
      <button v-if="query" class="archive-clear" aria-label="清空搜索" @click="query = ''; $refs.search.focus()"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m6 6 12 12M6 18 18 6" /></svg></button>
    </div>
    <div class="archive-content" :aria-busy="loading">
      <div v-if="loading" class="archive-state" role="status"><p>正在加载归档会话…</p></div>
      <div v-else-if="readError" class="archive-state" role="alert">
        <p class="archive-state-title">暂时无法加载归档会话</p><p class="archive-state-description">{{ readError }}</p>
        <div class="archive-state-actions"><button class="archive-action" @click="reload">重试</button><button class="archive-action" @click="refreshAfterWrite">刷新日常列表</button></div>
      </div>
      <div v-else-if="!rows.length" class="archive-state" role="status">
        <div class="archive-empty-icon"><svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 3h18v4H3zM5 7v14h14V7M10 11h4" /></svg></div>
        <p class="archive-state-title">{{ query.trim() ? '未找到匹配的归档会话' : '暂无归档会话' }}</p>
        <p class="archive-state-description">{{ query.trim() ? '试试其他关键词，或清空搜索查看全部' : '归档后的会话会显示在这里，可随时取消归档' }}</p>
      </div>
      <ul v-else class="archive-rows"><li v-for="session in rows" :key="session.session_id">
        <span class="archive-title" :title="session.title">{{ session.title || '未命名会话' }}</span>
        <div class="archive-row-actions"><button class="archive-action" :disabled="!!busy[session.session_id]" @click="operate(session)">取消归档</button><button class="archive-action archive-delete" :disabled="!!busy[session.session_id]" @click="operate(session, true)">删除</button></div>
      </li></ul>
    </div>
    <footer v-if="total > 0 && !readError" class="archive-footer">
      <span class="archive-count">共 {{ total }} 个会话</span>
      <nav v-if="pages > 1" aria-label="归档分页" class="archive-pagination"><button class="archive-page-button" :disabled="loading || page <= 1" @click="goPage(page - 1)">上一页</button><span>{{ page }} / {{ pages }}</span><button class="archive-page-button" :disabled="loading || page >= pages" @click="goPage(page + 1)">下一页</button></nav>
    </footer>
</section>
  <p v-if="notice" class="archive-notice" role="status">{{ notice }}</p>
  <ConfirmDialog ref="confirm" :title="confirmTitle" :message="confirmMessage" confirm-text="确定" />
</div></template>
<script>
import archiveManager from '../../../shared/sessionManagement/archiveManager.js'
import ConfirmDialog from '../../../shared/components/ConfirmDialog.vue'
export default {
  name: 'ArchiveManager', mixins: [archiveManager], components: { ConfirmDialog },
  beforeUnmount() { this.disposeArchive() },
  mounted() { if (this.open) this.reload() },
}
</script>
<style scoped>
.archive-manager.is-open { display: flex; flex: 1; min-height: 0; }
.archive-panel { flex: 1; min-width: 0; display: flex; flex-direction: column; color: var(--text-primary); font-size: 14px; line-height: 1.5; }
.archive-header { margin-bottom: 16px; }
.archive-header h3 { margin: 0 0 4px; font-size: 16px; font-weight: 600; }
.archive-header p { margin: 0; font-size: 13px; color: var(--text-secondary); }
.archive-search { display: flex; align-items: center; gap: 8px; height: 40px; padding: 0 12px; border: 1px solid var(--border-color); border-radius: 8px; background: var(--bg-secondary); color: var(--text-secondary); }
.archive-search:focus-within { border-color: var(--primary-color); }
.archive-search svg { display: block; width: 18px; height: 18px; flex-shrink: 0; }
.archive-search input { flex: 1; width: 100%; min-width: 0; height: 100%; padding: 0; border: 0; outline: none; background: transparent; color: var(--text-primary); font: inherit; }
.archive-search input::placeholder { color: var(--text-secondary); }
.archive-clear { display: flex; align-items: center; justify-content: center; flex-shrink: 0; width: 28px; height: 28px; padding: 4px; border: 0; border-radius: 4px; background: transparent; color: var(--text-secondary); cursor: pointer; }
.archive-content { flex: 1; min-height: 0; margin-top: 12px; display: flex; flex-direction: column; overflow-y: auto; }
.archive-state { flex: 1; min-height: 224px; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 224px; padding: 24px 16px; box-sizing: border-box; text-align: center; color: var(--text-secondary); }
.archive-state p { margin: 0; }
.archive-empty-icon { display: flex; align-items: center; justify-content: center; width: 56px; height: 56px; margin-bottom: 16px; border-radius: 50%; background: var(--bg-tertiary); color: var(--text-secondary); }
.archive-empty-icon svg { width: 28px; height: 28px; }
.archive-state .archive-state-title { font-size: 14px; font-weight: 500; color: var(--text-primary); }
.archive-state .archive-state-description { margin-top: 8px; font-size: 12px; overflow-wrap: anywhere; }
.archive-state-actions { display: flex; gap: 8px; margin-top: 16px; }
.archive-rows { list-style: none; margin: 0; padding: 0; }
.archive-rows li { display: flex; gap: 16px; align-items: center; min-height: 56px; padding: 12px 0; box-sizing: border-box; border-bottom: 1px solid var(--border-color); }
.archive-rows li:last-child { border-bottom: 0; }
.archive-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.archive-row-actions { display: flex; flex-shrink: 0; gap: 8px; }
.archive-action, .archive-page-button { cursor: pointer; padding: 6px 10px; border: 1px solid var(--border-color); border-radius: 6px; background: var(--bg-secondary); color: var(--text-primary); font: inherit; font-size: 12px; white-space: nowrap; }
.archive-delete { color: var(--text-secondary); }
.archive-action:hover:not(:disabled), .archive-page-button:hover:not(:disabled), .archive-clear:hover { background: var(--bg-tertiary); }
.archive-manager button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
.archive-manager button:disabled { opacity: .5; cursor: default; }
.archive-footer { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; padding-top: 16px; border-top: 1px solid var(--border-color); font-size: 12px; color: var(--text-secondary); }
.archive-pagination { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.archive-count { white-space: nowrap; }
.archive-notice { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); z-index: 3000; padding: 12px 18px; background: var(--bg-secondary); color: var(--text-primary); border: 1px solid var(--border-color); border-radius: 8px; box-shadow: 0 4px 18px #0002; }
@media (max-width: 480px) {
  .archive-rows li { gap: 8px; }
  .archive-row-actions { gap: 4px; }
  .archive-row-actions .archive-action { padding: 6px; }
}
</style>
