<template>
  <div class="host-shell">
    <header class="host-header">
      <span class="host-brand">宿主门户</span>
      <span class="host-title-probe">{{ $route.meta.title || '' }}</span>
      <el-button size="mini" type="primary" class="host-header-btn">宿主按钮</el-button>
    </header>
    <div class="host-body">
      <nav class="host-nav">
        <ul>
          <li><router-link to="/home">首页</router-link></li>
          <li><router-link to="/easy-agent">Easy Agent</router-link></li>
          <li><router-link to="/other?keep=1#frag">其他业务</router-link></li>
        </ul>
        <p class="host-nav-note"><span>宿主 span 文字</span></p>
      </nav>
      <main class="host-content">
        <keep-alive :include="keepAliveNames">
          <router-view />
        </keep-alive>
      </main>
    </div>
  </div>
</template>

<script>
export default {
  name: 'HostLayout',
  computed: {
    // ?keepalive=1 时缓存 Easy Agent 页面，用来验证 activated / deactivated
    keepAliveNames() {
      return this.$route.query.keepalive === '1' || window.__keepAlive ? ['EasyAgentRoute'] : []
    },
  },
}
</script>

<style scoped>
.host-shell { height: 100%; display: flex; flex-direction: column; }
.host-header { height: 56px; flex: none; display: flex; align-items: center; gap: 16px; padding: 0 20px; background: #001529; color: #fff; }
.host-brand { font-weight: 700; }
.host-body { flex: 1; min-height: 0; display: flex; }
.host-nav { width: 180px; flex: none; background: #f0f2f5; padding: 12px; }
.host-content { flex: 1; min-width: 0; min-height: 0; position: relative; }
</style>
