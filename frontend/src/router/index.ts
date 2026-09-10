import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'
import Plugins from '@/views/Plugins.vue'
import Pipelines from '@/views/Pipelines.vue'
import Agents from '@/views/Agents.vue'
import AgentChat from '@/views/AgentChat.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
    { path: '/plugins', name: 'plugins', component: Plugins },
    { path: '/pipelines', name: 'pipelines', component: Pipelines },
    { path: '/agents', name: 'agents', component: Agents },
    { path: '/agents/:id/chat', name: 'agent-chat', component: AgentChat },
  ],
})

export default router
