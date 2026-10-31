<template>
  <!-- 外网独立应用的薄启动壳：整页只渲染业务模块根组件（业务实现全部在 packages/easyAgent） -->
  <EasyAgentPage />
</template>

<script>
import { EasyAgentPage } from './packages/easyAgent/index.js'

export default {
  name: 'App',
  components: { EasyAgentPage },
}
</script>
