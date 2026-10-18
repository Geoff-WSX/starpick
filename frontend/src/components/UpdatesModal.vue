<script setup lang="ts">
// 更新检查历史弹窗（原独立页迁移；与采集轮次历史分开保存）
import { ref, watch } from 'vue'
import { fetchUpdateRounds, type UpdateRound } from '../api'

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
  const map: Record<string, string> = {
    running: '检查中', success: '成功', failed: '失败', skipped: '跳过',
  }
  return map[s] ?? s
}

function updateScopeLabel(s: string): string {
  return s === 'full' ? '全库' : s === 'incremental' ? '增量' : s
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchUpdateRounds(200)
    rounds.value = data.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载更新检查历史失败'
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
          <h2>更新检查历史</h2>
        <span class="hint">对已采集项目检查 GitHub 最新 release 的轮次记录；实际耗时含限流排队</span>
        </div>
        <button class="modal-close" title="关闭" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else-if="rounds.length === 0" class="empty">暂无更新检查记录，去「采集中心」点击「检查更新」</div>
      <div v-else class="rounds-table-wrap">
        <table class="rounds-table">
          <thead>
            <tr>
              <th>#</th>
              <th>范围</th>
              <th>开始</th>
              <th>结束</th>
              <th>状态</th>
              <th>已查</th>
              <th>发现新版</th>
              <th>无变化</th>
              <th>失败</th>
              <th title="整轮实际耗时（含 GitHub 限流排队等待）">实际耗时</th>
              <th>说明</th>
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
