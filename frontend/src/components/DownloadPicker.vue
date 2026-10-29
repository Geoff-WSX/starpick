<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { openExternal, detectOS, formatSize, type Project } from '../api'
import { t } from '../composables/useI18n'

const props = defineProps<{ project: Project }>()
const emit = defineEmits<{ close: [] }>()
const maskEl = ref<HTMLElement | null>(null)

onMounted(() => {
  // 让 mask 获得焦点，Esc 才能触发关闭（可访问性）
  maskEl.value?.focus()
})

// 平台展示顺序：当前系统优先，其余按固定顺序
const PLATFORM_ORDER = ['Windows', 'macOS', 'Linux', 'Android', 'iOS', 'Docker', 'Web']

const currentOS = detectOS()

const groups = computed(() => {
  const map = new Map<string, typeof props.project.assets>()
  for (const a of props.project.assets) {
    const key = a.platform || t('picker_other')
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(a)
  }
  const keys = [...map.keys()].sort((a, b) => {
    if (a === currentOS) return -1
    if (b === currentOS) return 1
    const ia = PLATFORM_ORDER.indexOf(a)
    const ib = PLATFORM_ORDER.indexOf(b)
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })
  return keys.map((k) => ({ platform: k, items: map.get(k)! }))
})

/** GitHub 直链 → 镜像加速链接（gh-proxy 系列：前缀 + 完整原始 URL 即转发） */
function mirrorUrl(url: string): string {
  const p = props.project.mirror_prefixes?.[0]
  if (!p || !url.startsWith('https://github.com/')) return ''
  return `${p}${url}`
}

function download(url: string) {
  openExternal(url)
}

/** 优先走镜像加速；无镜像前缀时退回原始链接 */
function downloadBest(url: string) {
  download(mirrorUrl(url) || url)
}

function openGithub() {
  openExternal(props.project.html_url)
}

// 支持 Esc 关闭弹窗（可访问性）
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') emit('close')
}
</script>

<template>
  <div ref="maskEl" class="picker-mask" tabindex="-1" @click.self="emit('close')" @keydown="onKeydown">
    <div class="picker">
      <div class="picker-head">
        <div>
          <div class="picker-title">{{ project.full_name }}</div>
          <div class="picker-sub">{{ t('picker_sub').replace('{os}', currentOS || t('picker_unknown')) }}</div>
        </div>
        <button class="modal-close" title="关闭" @click="emit('close')">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="project.assets.length === 0" class="empty">
        <p>{{ t('picker_none') }}</p>
        <button class="btn ghost" @click="openGithub">{{ t('picker_github') }}</button>
      </div>

      <div v-else class="picker-body">
        <div v-if="project.mirror_prefixes.length" class="hint mirror-hint">
          {{ t('picker_mirror_on').replace('{prefix}', project.mirror_prefixes[0]) }}
        </div>
        <div v-for="g in groups" :key="g.platform" class="pick-group">
          <div class="pick-platform" :class="{ match: g.platform === currentOS }">
            {{ g.platform }}
            <span v-if="g.platform === currentOS" class="pick-tag">{{ t('picker_current') }}</span>
          </div>
          <div v-for="a in g.items" :key="a.url" class="pick-row" @click="downloadBest(a.url)">
            <div class="pick-name" :title="a.name">{{ a.name }}</div>
            <div class="pick-size">{{ formatSize(a.size) }}</div>
            <button v-if="mirrorUrl(a.url)" class="btn dl small" @click.stop="downloadBest(a.url)">加速下载</button>
            <button class="btn small" :title="a.url" @click.stop="download(a.url)">原地址</button>
          </div>
        </div>
        <div class="hint">{{ t('picker_arch_hint') }}</div>
      </div>
    </div>
  </div>
</template>
