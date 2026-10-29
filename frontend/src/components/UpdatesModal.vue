<script setup lang="ts">
// 更新检查历史弹窗（原独立页迁移；与采集轮次历史分开保存）
import { ref, watch } from 'vue'
import { fetchUpdateRounds, type UpdateRound } from '../api'
import { t } from '../composables/useI18n'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const rounds = ref<UpdateRound[]>([])
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
  return `${m}分${s}秒`
}

function statusLabel(s: string): string {
  const map: Record<string, () => string> = {
    running: () => t('updates_status_running'), success: () => t('updates_status_success'), failed: () => t('updates_status_failed'), skipped: () => t('updates_status_skipped'),
  }
  return map[s] ? map[s]() : s
}

function mapError(e: string): string {
  if (e === '进程重启，旧运行轮次作废') return t('err_stale_round')
  return e
}

function updateScopeLabel(s: string): string {
  return s === 'full' ? t('updates_scope_full') : s === 'incremental' ? t('updates_scope_incremental') : s
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchUpdateRounds(200)
    rounds.value = data.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : t('updates_load_fail')
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
          <h2>{{ t('updates_title') }}</h2>
        <span class="hint">{{ t('updates_hint') }}</span>
        </div>
        <button class="modal-close" :title="t('collect_close')" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">{{ t('loading') }}</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else-if="rounds.length === 0" class="empty">{{ t('updates_empty') }}</div>
      <div v-else class="rounds-table-wrap">
        <table class="rounds-table">
          <thead>
            <tr>
              <th>#</th>
              <th>{{ t('updates_col_scope') }}</th>
              <th>{{ t('updates_col_start') }}</th>
              <th>{{ t('updates_col_end') }}</th>
              <th>{{ t('updates_col_status') }}</th>
              <th>{{ t('updates_col_checked') }}</th>
              <th>{{ t('updates_col_new') }}</th>
              <th>{{ t('updates_col_nochange') }}</th>
              <th>{{ t('updates_col_failed') }}</th>
              <th :title="t('updates_col_duration_tip')">{{ t('updates_col_duration') }}</th>
              <th>{{ t('updates_col_error') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in rounds" :key="r.id">
              <td class="dim">{{ r.id }}</td>
              <td>{{ updateScopeLabel(r.scope) }}</td>
              <td>{{ fmtTime(r.started_at) }}</td>
              <td>{{ fmtTime(r.finished_at) }}</td>
              <td><span class="round-status" :class="r.status">{{ statusLabel(r.status) }}</span></td>
              <td>{{ r.stats?.total ?? 0 }}</td>
              <td class="strong">{{ r.stats?.updated ?? 0 }}</td>
              <td>{{ r.stats?.nochange ?? 0 }}</td>
              <td :class="{ 'err-text': Number(r.stats?.failed ?? 0) > 0 }">{{ r.stats?.failed ?? 0 }}</td>
              <td>{{ fmtDuration(Number(r.stats?.duration_sec) || 0) }}</td>
              <td class="dim note">
                <span v-if="r.error" :title="mapError(r.error)">{{ mapError(r.error).length > 60 ? mapError(r.error).slice(0, 60) + '…' : mapError(r.error) }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
