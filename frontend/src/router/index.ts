import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'
import Plugins from '@/views/Plugins.vue'
import Pipelines from '@/views/Pipelines.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
    { path: '/plugins', name: 'plugins', component: Plugins },
    { path: '/pipelines', name: 'pipelines', component: Pipelines },
  ],
})

export default router
