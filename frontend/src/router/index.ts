import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'
import Plugins from '@/views/Plugins.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
    { path: '/plugins', name: 'plugins', component: Plugins },
    // 旧书签/链接兼容：流水线预览已并入 Runs 页的 drawer
    { path: '/pipelines', redirect: '/' },
  ],
})

export default router
