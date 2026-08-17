import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
  ],
})

export default router
