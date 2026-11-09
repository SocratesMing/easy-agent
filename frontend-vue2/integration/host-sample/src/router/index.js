import Vue from 'vue'
import VueRouter from 'vue-router'
import HomeView from '../views/HomeView.vue'
import easyAgentRoutes from './easyAgent.routes'

Vue.use(VueRouter)

export default new VueRouter({
  // hash 模式：query 在 # 之后，模块不能直接读 location.search
  mode: 'hash',
  routes: [
    { path: '/', redirect: '/home' },
    { path: '/home', component: HomeView, meta: { title: '首页' } },
    { path: '/other', component: () => import('../views/OtherView.vue'), meta: { title: '其他业务' } },
    { path: '/login', component: () => import('../views/LoginView.vue'), meta: { title: '登录' } },
    ...easyAgentRoutes,
  ],
})
