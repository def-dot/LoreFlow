<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, shallowRef } from 'vue'
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from '@codemirror/view'
import { EditorState, type Extension } from '@codemirror/state'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { indentOnInput, bracketMatching, foldGutter, foldKeymap, syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { highlightSelectionMatches, searchKeymap } from '@codemirror/search'
import { yaml } from '@codemirror/lang-yaml'
import { oneDark } from '@codemirror/theme-one-dark'

const props = withDefaults(defineProps<{
  modelValue: string
  placeholder?: string
  readOnly?: boolean
}>(), {
  placeholder: '',
  readOnly: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const editorRef = ref<HTMLElement>()
const view = shallowRef<EditorView>()

const baseTheme = EditorView.theme({
  '&': {
    fontSize: '13px',
    height: '100%',
  },
  '.cm-scroller': {
    fontFamily: 'var(--font-mono, ui-monospace, monospace)',
    overflow: 'auto',
  },
  '.cm-content': {
    padding: '8px 0',
  },
  '&.cm-focused': {
    outline: 'none',
  },
  '.cm-gutters': {
    backgroundColor: 'transparent',
    borderRight: '1px solid var(--line, rgba(255,255,255,0.06))',
    color: 'var(--ink-3, rgba(255,255,255,0.25))',
  },
  '.cm-activeLineGutter': {
    backgroundColor: 'rgba(255, 255, 255, 0.04)',
  },
  '.cm-activeLine': {
    backgroundColor: 'rgba(255, 255, 255, 0.02)',
  },
  '.cm-selectionBackground': {
    backgroundColor: 'rgba(77, 196, 178, 0.15) !important',
  },
  '&.cm-focused .cm-selectionBackground': {
    backgroundColor: 'rgba(77, 196, 178, 0.25) !important',
  },
  '.cm-matchingBracket': {
    backgroundColor: 'rgba(77, 196, 178, 0.2)',
    outline: 'none',
  },
  '.cm-foldGutter span': {
    color: 'var(--ink-3, rgba(255,255,255,0.25))',
    cursor: 'pointer',
  },
  '.cm-foldGutter span:hover': {
    color: 'var(--ink-1, #fff)',
  },
})

function makeExtensions(): Extension[] {
  const exts: Extension[] = [
    baseTheme,
    oneDark,
    lineNumbers(),
    highlightActiveLineGutter(),
    history(),
    indentOnInput(),
    bracketMatching(),
    closeBrackets(),
    highlightActiveLine(),
    highlightSelectionMatches(),
    foldGutter(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    yaml(),
    keymap.of([
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...searchKeymap,
      ...historyKeymap,
      ...foldKeymap,
    ]),
    EditorView.lineWrapping,
    EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        emit('update:modelValue', update.state.doc.toString())
      }
    }),
  ]
  if (props.readOnly) {
    exts.push(EditorState.readOnly.of(true))
    exts.push(EditorView.editable.of(false))
  }
  if (props.placeholder) {
    exts.push(EditorView.contentAttributes.of({ 'aria-placeholder': props.placeholder }))
  }
  return exts
}

onMounted(() => {
  if (!editorRef.value) return
  const state = EditorState.create({
    doc: props.modelValue,
    extensions: makeExtensions(),
  })
  view.value = new EditorView({ state, parent: editorRef.value })
})

onBeforeUnmount(() => {
  view.value?.destroy()
})

// Sync external value changes → editor (only when focused elsewhere)
watch(() => props.modelValue, (val) => {
  if (!view.value) return
  const current = view.value.state.doc.toString()
  if (current !== val) {
    view.value.dispatch({
      changes: { from: 0, to: current.length, insert: val },
    })
  }
})
</script>

<template>
  <div ref="editorRef" class="yaml-editor-wrap" />
</template>

<style scoped>
.yaml-editor-wrap {
  border: 1px solid var(--line, rgba(255, 255, 255, 0.06));
  border-radius: 6px;
  overflow: hidden;
  height: 100%;
  min-height: 200px;
}
</style>
