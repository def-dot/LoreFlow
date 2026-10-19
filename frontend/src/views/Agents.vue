<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAgentsStore } from '@/stores/agents'
import AgentForm from '@/components/AgentForm.vue'
import { ElMessageBox, ElMessage } from 'element-plus'
import type { AgentListItem } from '@/api/agents'
import { listTools, type ToolOut } from '@/api/registry'

const router = useRouter()
const store = useAgentsStore()

const showDrawer = ref(false)
const editingAgent = ref<AgentListItem | null>(null)
const allTools = ref<ToolOut[]>([])

onMounted(async () => {
  store.fetchAgents()
  allTools.value = await listTools()
})

/** 工具名列表 → 卡片显示标签：
 *  MCP 工具折叠为服务器名（部分选中带计数），其余展开为 label。
 *  不按「基础」这类域分组压缩——那是选择器里的组织概念，卡片上看不懂。
 */
function toolDisplay(tools: string[]) {
  const labelOf = new Map<string, string>()
  const mcpServer = new Map<string, string>()
  const mcpTotal = new Map<string, number>()
  for (const t of allTools.value) {
    labelOf.set(t.name, t.name)
    if (t.metadata?.source === 'mcp') {
      const server = t.metadata?.source_name || t.name
      mcpServer.set(t.name, server)
      mcpTotal.set(server, (mcpTotal.get(server) || 0) + 1)
    }
  }
  const mcpHit = new Map<string, number>()
  for (const n of tools) {
    const server = mcpServer.get(n)
    if (server) mcpHit.set(server, (mcpHit.get(server) || 0) + 1)
  }

  const labels: string[] = []
  const seen = new Set<string>()
  for (const n of tools) {
    const server = mcpServer.get(n)
    let label: string
    if (server) {
      const hit = mcpHit.get(server)!
      label = hit < (mcpTotal.get(server) || 0) ? `${server} (${hit})` : server
    } else {
      label = labelOf.get(n) || n
    }
    if (seen.has(label)) continue
    seen.add(label)
    labels.push(label)
  }
  return { shown: labels.slice(0, 5), more: labels.length > 5 ? labels.length - 5 : 0 }
}

function openCreate() {
  editingAgent.value = null
  showDrawer.value = true
}

async function openEdit(id: number) {
  await store.selectAgent(id)
  editingAgent.value = store.selectedAgent
  showDrawer.value = true
}

function handleSaved() {
  showDrawer.value = false
  editingAgent.value = null
  store.selectedAgent = null
}

async function handleDelete(id: number, name: string) {
  try {
    await ElMessageBox.confirm(`确定删除 Agent「${name}」？`, '确认删除', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await store.removeAgent(id)
    ElMessage.success('已删除')
  } catch {
    // 取消
  }
}

function enterChat(agentId: number) {
  router.push({ name: 'agent-chat', params: { id: agentId } })
}
</script>

<template>
  <div class="agents-page">
    <div class="page-header">
      <h2>智能体</h2>
      <el-button type="primary" size="small" @click="openCreate">
        + 新建 Agent
      </el-button>
    </div>

    <div v-if="!store.agents.length" class="empty-state">
      <p>还没有 Agent，点击上方按钮创建第一个。</p>
    </div>

    <div class="agent-grid">
      <div
        v-for="agent in store.agents"
        :key="agent.id"
        class="agent-card"
        @click="enterChat(agent.id)"
      >
        <div class="card-header">
          <span class="agent-name">{{ agent.name }}</span>
          <span class="agent-model muted" v-if="agent.model">{{ agent.model }}</span>
        </div>
        <p class="agent-desc">{{ agent.description || '暂无描述' }}</p>

        <!-- 工具和技能展示 -->
        <div class="card-capabilities">
          <div class="cap-section" v-if="agent.tools?.length">
            <span class="cap-label">🔧 工具</span>
            <div class="cap-tags">
              <span v-for="t in toolDisplay(agent.tools).shown" :key="t" class="cap-tag tool-tag">{{ t }}</span>
              <span v-if="toolDisplay(agent.tools).more" class="cap-tag tool-tag more-tag">+{{ toolDisplay(agent.tools).more }}</span>
            </div>
          </div>
          <div class="cap-section" v-if="agent.skills?.length">
            <span class="cap-label">📖 技能</span>
            <div class="cap-tags">
              <span v-for="s in agent.skills" :key="s" class="cap-tag skill-tag">{{ s }}</span>
            </div>
          </div>
        </div>

        <div class="card-actions" @click.stop>
          <el-button size="small" type="primary" @click="enterChat(agent.id)">
            对话
          </el-button>
          <el-button size="small" plain @click="openEdit(agent.id)">
            编辑
          </el-button>
          <el-button size="small" type="danger" plain @click="handleDelete(agent.id, agent.name)">
            删除
          </el-button>
        </div>
      </div>
    </div>

    <el-drawer
      v-model="showDrawer"
      :title="editingAgent ? '编辑 Agent' : '新建 Agent'"
      size="480px"
      :destroy-on-close="true"
    >
      <AgentForm :agent="editingAgent" @saved="handleSaved" />
    </el-drawer>
  </div>
</template>

<style scoped>
.agents-page {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 20px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}

.empty-state {
  text-align: center;
  padding: 60px 20px;
  color: var(--ink-3);
}

.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}

.agent-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 18px;
  cursor: pointer;
  transition: border-color 0.2s;
}
.agent-card:hover {
  border-color: var(--line-strong);
}

.card-header {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}

.agent-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--ink);
}

.agent-model {
  font-family: var(--font-mono);
  font-size: 11px;
}

.agent-desc {
  font-size: 13px;
  color: var(--ink-3);
  margin: 0 0 12px;
  line-height: 1.5;
  /* 两行截断 */
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.card-capabilities {
  margin-bottom: 14px;
}

.cap-section {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.cap-label {
  font-size: 11px;
  color: var(--ink-3);
  white-space: nowrap;
  flex-shrink: 0;
}

.cap-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cap-tag {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 7px;
  border-radius: 4px;
  line-height: 1.6;
}

.tool-tag {
  background: rgba(77, 196, 178, 0.1);
  color: var(--accent);
  border: 1px solid rgba(77, 196, 178, 0.2);
}
.more-tag {
  opacity: 0.7;
  font-style: italic;
}

.skill-tag {
  background: rgba(240, 194, 75, 0.08);
  color: var(--amber);
  border: 1px solid rgba(240, 194, 75, 0.2);
}

.kb-tag {
  background: rgba(135, 144, 176, 0.1);
  color: var(--slate);
  border: 1px solid rgba(135, 144, 176, 0.25);
}

.card-actions {
  display: flex;
  gap: 8px;
}
</style>
