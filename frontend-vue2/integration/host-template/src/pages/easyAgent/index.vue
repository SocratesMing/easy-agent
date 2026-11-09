<template>
  <!--
    Easy Agent 路由页面（首次接入时复制到宿主 src/pages/easyAgent/index.vue，之后归宿主维护）。
    父容器（宿主布局里包住 <router-view> 的元素）必须有确定高度，例如
    `height: calc(100vh - 顶栏高度)`，或 flex 列布局里 `flex: 1; min-height: 0`。
    本页面撑满该容器，模块不会占用 100vw / 100vh。
  -->
  <div class="easy-agent-route">
    <EasyAgentPage />
  </div>
</template>

<script>
// 先执行宿主适配器（configureEasyAgent），再使用模块
import '@/integrations/easyAgent'
import { EasyAgentPage } from '@/packages/easyAgent'

export default {
  // keep-alive 的 include / exclude 按这个名字匹配
  name: 'EasyAgentRoute',
  components: { EasyAgentPage },
}
</script>

<style scoped>
.easy-agent-route {
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.easy-agent-route > * {
  flex: 1;
  min-height: 0;
}
</style>
