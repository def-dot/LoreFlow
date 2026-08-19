import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'
import Pipelines from '@/views/Pipelines.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
    { path: '/pipelines', name: 'pipelines', component: Pipelines },
  ],
})

export default router
