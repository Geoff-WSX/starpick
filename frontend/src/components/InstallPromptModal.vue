<script setup lang="ts">
/** 安装提示词弹窗：先让用户看清整段提示词，再一键复制。
 *
 * 复制后粘贴给豆包等能操作电脑的 AI 助手（选「本地电脑模式」），由对方完成下载安装。
 * 站内只负责把项目上下文整理成提示词，不承担安装执行。
 */
import { onMounted, ref } from 'vue'
import { detectOS } from '../api'
import { t } from '../composables/useI18n'
import { buildInstallPrompt, copyText, type InstallPromptSource } from '../installPrompt'

const props = defineProps<{ project: InstallPromptSource; os?: string }>()
const emit = defineEmits<{ close: [] }>()

const maskEl = ref<HTMLElement | null>(null)
const prompt = ref('')
const toast = ref('')
let toastTimer: ReturnType<typeof setTimeout> | undefined

/** 全文展示：换行显示不横向滚动，超出高度时纵向滚动 */
const preStyle: Record<string, string> = {
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  maxHeight: '46vh',
  overflowY: 'auto',
  margin: '10px 0 0',
}

onMounted(() => {
  prompt.value = buildInstallPrompt(props.project, props.os ?? detectOS())
  // 让 mask 获得焦点，Esc 才能触发关闭（可访问性）
  maskEl.value?.focus()
})

// 支持 Esc 关闭弹窗（可访问性）
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}

async function copyAll() {
  const ok = await copyText(prompt.value)
  toast.value = ok
    ? t('prompt_copied')
    : t('prompt_copy_fail')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (toast.value = ''), 4000)
}
</script>

<template>
  <div ref="maskEl" class="modal-overlay" tabindex="-1" @click.self="emit('close')" @keydown="onKeydown">
    <div class="modal">
      <div class="modal-head">
        <h2>{{ t('prompt_title') }}</h2>
        <button class="modal-close" title="关闭" @click="emit('close')">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div class="hint">{{ t('prompt_hint') }}</div>

      <div class="markdown-body">
        <pre :style="preStyle"><code>{{ prompt }}</code></pre>
      </div>

      <div class="modal-history">
        <button class="btn" @click="copyAll">{{ t('prompt_copy_all') }}</button>
        <button class="btn ghost" @click="emit('close')">关闭</button>
      </div>

      <div v-if="toast" class="toast">{{ toast }}</div>
    </div>
  </div>
</template>
