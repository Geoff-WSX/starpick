<script setup lang="ts">
// 采集轮次历史弹窗（原独立页迁移；导航栏入口改弹窗模式）
import { ref, watch } from 'vue'
import { fetchRounds, type RoundInfo } from '../api'

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
  return `${m}分${s}秒`
}

function statusLabel(s: string): string {
  const map: Record<string, string> = {
    running: '采集中', success: '成功', failed: '失败', skipped: '跳过',
  }
  return map[s] ?? s
}

// 轮次来源标签：手动 / 定时发现轮 / 每日全量轮 / 启动补跑（旧数据无标记时视为手动）
function sourceLabel(s: string | undefined): string {
  const map: Record<string, string> = {
    manual: '手动', scheduled: '定时', startup: '启动补跑',
    discovery: '定时·发现', full: '定时·全量',
    'discovery-startup': '补跑·发现', 'full-startup': '补跑·全量',
  }
  return (s && map[s]) || '手动'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchRounds(200)
    rounds.value = data.items
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载轮次历史失败'
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
          <h2>采集轮次历史</h2>
        <span class="hint">每轮候选/新增/更新/跳过统计；实际耗时含分类搜索与 GitHub 限流排队</span>
        </div>
        <button class="modal-close" title="关闭" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else-if="rounds.length === 0" class="empty">暂无轮次记录，去「采集中心」开始第一轮</div>
      <div v-else class="rounds-table-wrap">
        <table class="rounds-table">
          <thead>
            <tr>
              <th>#</th>
              <th>开始</th>
              <th>结束</th>
              <th>来源</th>
              <th>状态</th>
              <th>候选</th>
              <th>新增</th>
              <th>更新</th>
              <th>跳过</th>
              <th title="整轮实际耗时（含分类搜索与 GitHub 限流排队等待）">实际采集耗时</th>
              <th>说明</th>
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
