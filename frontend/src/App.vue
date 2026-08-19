<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <div class="app-shell">
    <nav class="top-nav">
      <span class="brand">DAG Flow</span>
      <router-link to="/" class="nav-link" :class="{ active: route.name === 'runs' }">Runs</router-link>
      <router-link
        to="/pipelines"
        class="nav-link"
        :class="{ active: route.name === 'pipelines' }"
      >Pipelines</router-link>
    </nav>
    <router-view />
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
}
.top-nav {
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 0 20px;
  height: 46px;
  background: #1a1d23;
  border-bottom: 1px solid #2a2f38;
}
.brand {
  font-size: 16px;
  font-weight: 600;
  margin-right: 14px;
  color: #e5e7eb;
}
.nav-link {
  color: #9aa4b2;
  text-decoration: none;
  font-size: 14px;
}
.nav-link:hover {
  color: #e5e7eb;
}
.nav-link.active {
  color: #3b82f6;
}
</style>
