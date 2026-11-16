<template>
  <!-- 用户消息按纯文本展示，不走 markdown：用户随手输入的 ~ * _ \ < > 与行首的 # = - 都原样显示，
       贴进来的网址也不会被改写成来源卡片。linkifyText 先整体转义，再只给 http(s) 链接拼 <a>，可直接 v-html -->
  <div class="user-message-text" v-html="html"></div>
</template>

<script>
import { linkifyText } from '../../../markdownSetup.js'

export default {
  name: 'UserMessageText',
  props: {
    content: {
      type: String,
      default: '',
    },
  },
  computed: {
    html() {
      // 末尾的换行/空格没有展示意义，去掉免得气泡底部多一块空白；行首缩进保留
      return linkifyText(String(this.content || '').replace(/\s+$/, ''))
    },
  },
}
</script>

<style scoped>
.user-message-text {
  /* 保留用户输入的换行、连续空格与缩进（粘贴的代码、按空格对齐的表格文本） */
  white-space: pre-wrap;
}
</style>
