<script setup lang="ts">
// 设置弹窗：GitHub Token 配置（保存后热生效，无需重启）
import { ref, watch } from 'vue'
import { fetchSettings, saveToken, clearToken, type SettingsState } from '../api'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const info = ref<SettingsState | null>(null)
const tokenInput = ref('')
const savedHint = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    info.value = await fetchSettings()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '读取设置失败'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      tokenInput.value = ''
      savedHint.value = ''
      load()
    }
  },
)

async function submit() {
  const t = tokenInput.value.trim()
  if (!t) return
  saving.value = true
  error.value = ''
  try {
    info.value = await saveToken(t)
    tokenInput.value = ''
    savedHint.value = '已保存并立即生效（无需重启）'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    saving.value = false
  }
}

async function remove() {
  saving.value = true
  error.value = ''
  try {
    info.value = await clearToken()
    savedHint.value = '已清除，回退匿名模式'
  } catch (e) {
    error.value = e instanceof Error ? e.message : '清除失败'
  } finally {
    saving.value = false
  }
}

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click.self="close">
    <div class="modal">
      <div class="modal-head">
        <h2>设置</h2>
        <button class="modal-close" title="关闭" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else class="settings-body">
        <!-- GitHub Token 配置 -->
        <div class="settings-group">
          <div class="settings-title">
            <span>GitHub Token</span>
            <span class="tag" :class="{ upd: info?.has_token }">
              {{ info?.has_token ? '已配置' : '未配置（匿名模式）' }}
            </span>
          </div>
          <p class="settings-desc">
            配置后 GitHub API 限流大幅提升（搜索 10→30 次/分钟、普通请求 60/小时→5000/小时），一轮全量采集由约 20 分钟降至约 3-5 分钟。Token 免费：GitHub → Settings → Developer settings → Personal access tokens。
          </p>
          <div v-if="info?.has_token" class="settings-current">
            当前生效：<code class="token-mask">{{ info.masked_token }}</code>
            <span class="dim">（仅显示部分字符）</span>
          </div>
          <div class="settings-row">
            <input
              v-model="tokenInput"
              class="settings-input"
              type="password"
              placeholder="粘贴 GitHub Token（ghp_…）"
              autocomplete="off"
              spellcheck="false"
              @keyup.enter="submit"
            />
            <button class="btn" :disabled="saving || !tokenInput.trim()" @click="submit">保存并生效</button>
            <button v-if="info?.has_token" class="btn ghost" :disabled="saving" @click="remove">清除 Token</button>
          </div>
          <div v-if="savedHint" class="settings-hint ok-hint">{{ savedHint }}</div>
        </div>

        <!-- 限流状态 -->
        <div class="settings-group" v-if="info">
          <div class="settings-title"><span>当前限流</span></div>
          <div class="settings-rate">
            <div class="rate-item">
              <span class="rate-label">搜索请求</span>
              <span class="rate-val">{{ info.search_rate_per_minute }} 次/分钟</span>
            </div>
            <div class="rate-item">
              <span class="rate-label">普通请求</span>
              <span class="rate-val">{{ info.core_rate_per_hour }} 次/小时</span>
            </div>
          </div>
          <p class="settings-desc">保存 Token 后此处数值立即变化，无需重启服务。</p>
        </div>
      </div>
    </div>
  </div>
</template>
