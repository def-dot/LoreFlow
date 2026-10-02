<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createSkill, updateSkill, deleteSkill, uploadSkillZip, type SkillCreateIn, type SkillDef } from '@/api/registry'

const props = defineProps<{
  skill?: SkillDef | null
}>()

const emit = defineEmits<{
  saved: []
}>()

type FormMode = 'input' | 'zip'

const isEdit = computed(() => !!props.skill)
const mode = ref<FormMode>('input')
const content = ref('')
const saving = ref(false)
const originalName = ref('')
const uploading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

/** 从 skill 对象获取 SKILL.md 内容 */
function skillToContent(s: SkillDef): string {
  return s.content
}

/** 解析 frontmatter */
function parseFrontmatter(text: string): { meta: Record<string, string>; body: string } | null {
  const match = text.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/)
  if (!match) return null
  const meta: Record<string, string> = {}
  for (const line of match[1].split('\n')) {
    const idx = line.indexOf(':')
    if (idx < 0) continue
    const key = line.slice(0, idx).trim()
    const val = line.slice(idx + 1).trim()
    if (key) meta[key] = val
  }
  return { meta, body: match[2].trim() }
}

const parsed = computed(() => {
  if (mode.value === 'zip') return { ok: true as const, meta: {}, body: '' }
  const result = parseFrontmatter(content.value)
  if (!result) return { ok: false as const, error: '缺少 frontmatter（需要 --- 包裹）' }
  if (!result.meta.name) return { ok: false as const, error: 'frontmatter 中缺少 name 字段' }
  return { ok: true as const, meta: result.meta, body: result.body }
})

watch(
  () => props.skill,
  (s) => {
    mode.value = 'input'
    if (s) {
      originalName.value = s.name
      content.value = skillToContent(s)
    } else {
      originalName.value = ''
      content.value = `---
name: my-skill
description: 一句话说明这个技能的用途
---

执行任务时，按以下步骤逐项检查：

## 步骤

1. 第一步
2. 第二步
3. 第三步

## 注意事项

- 要点一
- 要点二`
    }
  },
  { immediate: true },
)

async function handleSave() {
  if (!parsed.value.ok) {
    ElMessage.warning(parsed.value.error)
    return
  }
  const data: SkillCreateIn = {
    content: content.value,
  }
  saving.value = true
  try {
    if (isEdit.value) {
      await updateSkill(originalName.value, data)
      ElMessage.success('已保存')
    } else {
      await createSkill(data)
      ElMessage.success('已创建')
    }
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.msg || e?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function handleZip(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  input.value = ''

  // 编辑模式：确认替换
  if (isEdit.value && props.skill) {
    try {
      await ElMessageBox.confirm(`用新 zip 替换「${props.skill.name}」？目录中所有文件将被替换。`, '从 zip 替换', {
        type: 'warning',
        confirmButtonText: '替换',
        cancelButtonText: '取消',
      })
    } catch { return }
    uploading.value = true
    try {
      await deleteSkill(props.skill.name)
      await uploadSkillZip(file)
      ElMessage.success(`已替换 ${props.skill.name}`)
      emit('saved')
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.msg || e?.message || '替换失败')
    } finally {
      uploading.value = false
    }
  } else {
    // 新建模式：直接导入
    uploading.value = true
    try {
      const r = await uploadSkillZip(file)
      ElMessage.success(`导入成功，发现 ${r.count} 个技能`)
      emit('saved')
    } catch (e: any) {
      ElMessage.error(e?.response?.data?.msg || e?.message || '导入失败')
    } finally {
      uploading.value = false
    }
  }
}
</script>

<template>
  <div class="skill-form">
    <!-- 模式切换 -->
    <el-radio-group v-model="mode" size="small" class="mode-switch">
      <el-radio-button value="input">编写 SKILL.md</el-radio-button>
      <el-radio-button value="zip">导入 zip 包</el-radio-button>
    </el-radio-group>

    <!-- 编写模式 -->
    <template v-if="mode === 'input'">
      <el-alert v-if="!parsed.ok && content.trim()" :title="parsed.error" type="warning" show-icon :closable="false" />
      <el-input
        v-model="content"
        type="textarea"
        :rows="24"
        placeholder="---&#10;name: my-skill&#10;description: 一句话说明&#10;---&#10;&#10;指令正文..."
        spellcheck="false"
        class="mono"
      />
    </template>

    <!-- 导入模式 -->
    <div v-if="mode === 'zip'" class="zip-area">
      <input ref="fileInput" type="file" accept=".zip" hidden @change="handleZip" />
      <p class="muted">
        <template v-if="isEdit">选择 <code>.zip</code> 替换当前技能全部文件。zip 内直接包含 SKILL.md 等文件，修改 name 字段可重命名。</template>
        <template v-else>选择 <code>.zip</code> 导入技能。zip 内直接包含 SKILL.md 等文件（不需要外层目录），<code>name</code> 取自 SKILL.md。</template>
      </p>
      <el-button :loading="uploading" @click="fileInput?.click()">选择 zip 文件</el-button>
    </div>

    <div class="actions">
      <el-button
        v-if="mode === 'input'"
        type="primary"
        :loading="saving"
        :disabled="!parsed.ok"
        @click="handleSave"
      >
        {{ isEdit ? '保存修改' : '创建' }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.skill-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mode-switch {
  display: flex;
  width: 100%;
}
.mode-switch :deep(.el-radio-button) {
  flex: 1;
}
.mode-switch :deep(.el-radio-button__inner) {
  width: 100%;
}
.zip-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 40px 20px;
  border: 1px dashed var(--line);
  border-radius: 8px;
}
.zip-area .muted {
  font-size: 13px;
  text-align: center;
}
.zip-area code {
  font-family: var(--font-mono);
  background: rgba(255, 255, 255, 0.06);
  padding: 1px 5px;
  border-radius: 3px;
}
.mono :deep(textarea) {
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.6;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 4px;
}
</style>