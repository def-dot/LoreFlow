<script setup lang="ts">
import { useRoute } from 'vue-router'
import { useRunsStore } from '@/stores/runs'

const route = useRoute()
const runsStore = useRunsStore()
// 电流条件：存在未完结的 run（含等待人工审核）
</script>

<template>
  <div class="app-shell">
    <!-- flowing：有 run 在执行时底部出现一条流动的青色电流（页面生命体征） -->
    <nav class="top-nav" :class="{ flowing: runsStore.hasActive }">
      <span class="brand">
        <!-- 迷你 DAG：青点 = 流转节点，琥珀空心点 = 人工闸门 -->
        <svg class="brand-mark" width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
          <path d="M6 9 L13 4.5 M6 9 L13 13.5" stroke="#3a4470" stroke-width="1.5" fill="none" />
          <circle cx="4.5" cy="9" r="2.2" fill="#4dc4b2" />
          <circle cx="14" cy="4.5" r="2.2" fill="none" stroke="#f0c24b" stroke-width="1.5" />
          <circle cx="14" cy="13.5" r="2.2" fill="#2e3860" />
        </svg>
        LoreFlow
      </span>
      <router-link to="/" class="nav-link" :class="{ active: route.name === 'runs' }">运行</router-link>
      <router-link to="/plugins" class="nav-link" :class="{ active: route.name === 'plugins' }">节点类型</router-link>
    </nav>
    <router-view />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}
.top-nav {
  position: relative;
  display: flex;
  align-items: center;
  gap: 22px;
  padding: 0 20px;
  height: 52px;
  background: rgba(10, 14, 27, 0.92);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(6px);
}
.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  margin-right: 10px;
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  letter-spacing: 0.05em;
  color: var(--ink);
}
.brand-mark {
  flex: none;
}
.nav-link {
  position: relative;
  padding: 5px 2px;
  color: var(--ink-2);
  text-decoration: none;
  font-size: 13px;
}
.nav-link:hover {
  color: var(--ink);
}
.nav-link.active {
  color: var(--ink);
}
.nav-link.active::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 2px;
  border-radius: 2px;
  background: var(--accent);
}
/* 签名元素：执行中的“电流” —— 一段青色光斑沿导航底边循环流动 */
.top-nav::after {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  bottom: -1px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(77, 196, 178, 0.8), transparent);
  background-size: 220px 100%;
  background-repeat: no-repeat;
  opacity: 0;
  transition: opacity 0.5s;
}
.top-nav.flowing::after {
  opacity: 1;
  animation: nav-flow 2.6s linear infinite;
}
@keyframes nav-flow {
  from {
    background-position-x: -220px;
  }
  to {
    background-position-x: calc(100% + 220px);
  }
}
</style>
