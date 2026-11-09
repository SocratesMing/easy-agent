<template>
  <!-- 异常兜底说明：这一轮没跑完（超时 / 上游断连等），常驻在正文之后，不弹框、不自动消失。
       fallback_note 由后端异常兜底写进消息并落库，刷新/切会话后仍在；
       历史消息没有该字段时什么都不渲染。 -->
  <div v-if="note" class="message-fallback-note">
    <svg class="fallback-note-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
      <circle cx="12" cy="12" r="9"></circle>
      <line x1="12" y1="7.5" x2="12" y2="13"></line>
      <line x1="12" y1="16.5" x2="12.01" y2="16.5"></line>
    </svg>
    <span>{{ note }}</span>
  </div>
</template>

<script setup>
// 从 ChatMessage.vue 抽出（超大文件行数预算），展示 message.fallback_note
defineProps({
  note: { type: String, default: '' },
})
</script>

<style scoped>
/* 超时兜底说明：比正文轻一档的中性提示条，常驻不消失。
   只用主题变量 + Flex + 圆角，不用 color-mix（Edge 92 不支持），
   也不依赖 flex gap，旧内核同样正常。 */
.message-fallback-note {
  display: flex;
  align-items: flex-start;
  margin-top: 12px;
  padding: 10px 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.message-fallback-note .fallback-note-icon {
  flex: 0 0 auto;
  width: 15px;
  height: 15px;
  margin-top: 3px;
  margin-right: 8px;
  color: #BA7517;
}

.message-fallback-note span {
  flex: 1 1 auto;
  min-width: 0;
}
</style>
