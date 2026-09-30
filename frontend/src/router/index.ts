import { createRouter, createWebHistory } from 'vue-router'
import Runs from '@/views/Runs.vue'
import Capabilities from '@/views/Capabilities.vue'
import Pipelines from '@/views/Pipelines.vue'
import Agents from '@/views/Agents.vue'
import AgentChat from '@/views/AgentChat.vue'
import Knowledge from '@/views/Knowledge.vue'
import Chunks from '@/views/Chunks.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'runs', component: Runs },
    { path: '/capabilities', name: 'capabilities', component: Capabilities },
    { path: '/plugins', redirect: '/capabilities' },
    { path: '/pipelines', name: 'pipelines', component: Pipelines },
    { path: '/agents', name: 'agents', component: Agents },
    { path: '/agents/:id/chat', name: 'agent-chat', component: AgentChat },
    { path: '/knowledge', name: 'knowledge', component: Knowledge },
    { path: '/knowledge/:id/chunks', name: 'Chunks', component: Chunks },
  ],
})

export default router
