<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import FieldValues, { type FieldValue } from './FieldValues.vue'

const props = defineProps<{ reviewing: { name: string; label: string; description: string | null; payload: unknown }[]; deciding: boolean }>()

const emit = defineEmits<{
  decide: [node: string, approve: boolean, reason: string | null, values: Record<string, string> | null]
}>()

const reasons = reactive<Record<string, string>>({})
// 审核草稿：node → (字段键 → 当前文本)。初始即原文，编辑即更新；
// 「通过」时整体送回（未改字段送回的就是原值）
const drafts = reactive<Record<string, Record<string, string>>>({})
// node → 本轮草稿对应的 payload 引用：payload 变了（新一轮审核/loop 新迭代）即重置
const draftBases = new Map<string, unknown>()

interface ReviewCardModel {
  name: string
  label: string
  prompt: string | null    // 作者给的把关指引（节点 description）
  fields: FieldValue[] | null  // null = payload 不是对象，只能整体按 JSON 展示
  raw: string                   // 非对象 payload 的兜底 JSON 文本
  editable: boolean             // 存在可就地修改的字符串字段
  source: unknown               // payload 引用（草稿重置基准）
}

function payloadText(payload: unknown): string {
  return payload === null || payload === undefined ? '' : JSON.stringify(payload, null, 2)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

// 逐字段渲染：label 优先取引擎下发的 _review（声明式审核视图），未声明
// 视图的 payload 同样按字段展示（label=键名）。_review 是引擎保留键，
// 不进字段列表；description（把关指引）从节点元数据获取，单独渲染在卡片顶部

// _review 是 YAML 声明原文 {key: 标签文本} 直通；空标签回退键名
function labelOf(labels: Record<string, unknown>, key: string): string {
  const label = labels[key]
  return typeof label === 'string' && label ? label : key
}
const cards = computed<ReviewCardModel[]>(() =>
  props.reviewing.map((item) => {
    const payload = item.payload
    const raw = payloadText(payload)
    if (!isRecord(payload)) {
      return { name: item.name, label: item.label, prompt: item.description, fields: null, raw, editable: false, source: payload }
    }
    const prompt = item.description
    const labels = isRecord(payload._review) ? payload._review : {}
    const fields: FieldValue[] = Object.keys(payload)
      .filter((key) => key !== '_review')
      .map((key) => ({
        key,
        label: labelOf(labels, key),
        value: payload[key],
      }))
    return {
      name: item.name,
      label: item.label,
      prompt,
      fields,
      raw,
      editable: fields.some((f) => typeof f.value === 'string'),
      source: payload,
    }
  }),
)

// 草稿初值随卡片就位：字符串字段以原文起步；payload 引用变化时重置该节点
watch(
  cards,
  (list) => {
    for (const card of list) {
      if (draftBases.get(card.name) !== card.source) {
        draftBases.set(card.name, card.source)
        delete drafts[card.name]
      }
      const node = (drafts[card.name] ??= {})
      for (const f of card.fields ?? []) {
        if (typeof f.value === 'string' && !(f.key in node)) node[f.key] = f.value
      }
    }
  },
  { immediate: true },
)

// 通过：送回全部文本字段的当前值（drafts 初始即原文、编辑即更新——
// 未改字段送回的就是原值；卡片无文本字段则不带 values）
function approveCard(card: ReviewCardModel) {
  const values = drafts[card.name] ?? {}
  emit('decide', card.name, true, null, Object.keys(values).length ? values : null)
}
</script>

<template>
  <div v-if="!reviewing.length" class="muted">暂无待审核节点。</div>
  <div v-for="card in cards" :key="card.name" class="review-card">
    <h3>{{ card.label }}</h3>
    <!-- 把关指引：作者经 prompt 声明的决策标准，置顶常驻 -->
    <p v-if="card.prompt" class="prompt">{{ card.prompt }}</p>

    <!-- 声明式视图 / 对象 payload：逐字段展示（label + 值），文本字段可修订 -->
    <FieldValues v-if="card.fields" :fields="card.fields" :drafts="drafts[card.name]" />
    <!-- 非对象 payload 兜底：整体 JSON -->
    <pre v-else class="payload">{{ card.raw }}</pre>

    <el-input v-model="reasons[card.name]" placeholder="拒绝原因（可选）" size="small" />
    <div class="buttons">
      <el-button
        type="success"
        size="small"
        :loading="deciding"
        :disabled="deciding"
        @click="approveCard(card)"
      >
        ✓ 通过
      </el-button>
      <el-button
        type="danger"
        size="small"
        :loading="deciding"
        :disabled="deciding"
        @click="emit('decide', card.name, false, reasons[card.name] || null, null)"
      >
        ✕ 驳回
      </el-button>
    </div>
  </div>
</template>

<style scoped>
/* 人工介入时刻：抬升面板 + 琥珀左条（与 running 的青色形成机器/人对位） */
.review-card {
  background: rgba(22, 28, 54, 0.85);
  border: 1px solid var(--line);
  border-left: 3px solid var(--amber);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 12px;
}
.review-card h3 {
  margin: 0 0 8px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--ink);
}
/* 把关指引：琥珀左边条呼应卡片的人-HITL 语义 */
.prompt {
  margin: 0 0 10px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ink-2);
}
.payload {
  margin: 0 0 10px;
  max-height: 200px;
  overflow: auto;
  padding: 10px;
  background: #0c1122;
  border: 1px solid #1a2038;
  border-radius: 8px;
  color: var(--ink-2);
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  white-space: pre-wrap;
}
.buttons {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
</style>
