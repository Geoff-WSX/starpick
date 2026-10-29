<script setup lang="ts">
// 采集轮次历史弹窗（原独立页迁移；导航栏入口改弹窗模式）
import { ref, watch } from 'vue'
import { fetchRounds, type RoundInfo } from '../api'
import { t } from '../composables/useI18n'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const rounds = ref<RoundInfo[]>([])
const loading = ref(false)
const error = ref('')

function fmtTime(iso: string | null): string {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function fmtDuration(sec: number | undefined): string {
  if (!sec || sec <= 0) return '—'
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m ${s}s`
}

function statusLabel(s: string): string {
  const map: Record<string, () => string> = {
    running: () => t('rounds_status_running'), success: () => t('rounds_status_success'), failed: () => t('rounds_status_failed'), skipped: () => t('rounds_status_skipped'),
  }
  return map[s] ? map[s]() : s
}

// 轮次来源标签：手动 / 定时发现轮 / 每日全量轮 / 启动补跑（旧数据无标记时视为手动）
function sourceLabel(s: string | undefined): string {
  const map: Record<string, () => string> = {
    manual: () => t('rounds_src_manual'), scheduled: () => t('rounds_src_scheduled'), startup: () => t('rounds_src_startup'),
    discovery: () => t('rounds_src_discovery'), full: () => t('rounds_src_full'),
    'discovery-startup': () => t('rounds_src_disc_startup'), 'full-startup': () => t('rounds_src_full_startup'),
  }
  return (s && map[s]) ? map[s]() : t('rounds_src_manual')
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchRounds(200)
    rounds.value = data.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('rounds_load_fail')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) load()
  },
)

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click.self="close">
    <div class="modal modal-wide">
      <div class="modal-head">
        <div class="modal-title">
          <h2>{{ t('rounds_title') }}</h2>
        <span class="hint">{{ t('rounds_hint') }}</span>
        </div>
        <button class="modal-close" :title="t('collect_close')" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">{{ t('loading') }}</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else-if="rounds.length === 0" class="empty">暂无轮次记录，去「采集中心」开始第一轮</div>
      <div v-else class="rounds-table-wrap">
        <table class="rounds-table">
          <thead>
            <tr>
              <th>#</th>
              <th>{{ t('rounds_col_start') }}</th>
              <th>{{ t('rounds_col_end') }}</th>
              <th>{{ t('rounds_col_source') }}</th>
              <th>{{ t('rounds_col_status') }}</th>
              <th>{{ t('rounds_col_candidates') }}</th>
              <th>{{ t('rounds_col_inserted') }}</th>
              <th>{{ t('rounds_col_updated') }}</th>
              <th>{{ t('rounds_col_skipped') }}</th>
              <th :title="t('rounds_col_duration_tip')">{{ t('rounds_col_duration') }}</th>
              <th>{{ t('rounds_col_error') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rounds" :key="r.id">
              <td class="dim">{{ r.id }}</td>
              <td>{{ fmtTime(r.started_at) }}</td>
              <td>{{ fmtTime(r.finished_at) }}</td>
              <td><span class="round-source">{{ sourceLabel(r.source) }}</span></td>
              <td><span class="round-status" :class="r.status">{{ statusLabel(r.status) }}</span></td>
              <td>{{ r.stats?.candidates ?? 0 }}</td>
              <td class="strong">{{ r.stats?.inserted ?? 0 }}</td>
              <td>{{ r.stats?.updated ?? 0 }}</td>
              <td>{{ r.stats?.skipped ?? 0 }}</td>
              <td>{{ fmtDuration(r.stats?.duration_sec) }}</td>
              <td class="dim note">
                <span v-if="r.error" :title="r.error">{{ r.error.length > 60 ? r.error.slice(0, 60) + '…' : r.error }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
