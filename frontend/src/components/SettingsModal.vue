<script setup lang="ts">
// 设置弹窗：GitHub Token 配置（保存后热生效）+「关于」（版本 / 检查更新 / 项目地址）
import { computed, ref, watch } from 'vue'
import {
  fetchSettings, saveToken, clearToken, checkAppUpdate, openExternal,
  type SettingsState,
} from '../api'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const loading = ref(false)
const saving = ref(false)
const error = ref('')
const info = ref<SettingsState | null>(null)
const tokenInput = ref('')
const savedHint = ref('')

// 选项卡：tab=「配置」（Token+限流）/「about」=「关于」/「models」=「模型中转站推荐」
const tab = ref<'tab' | 'about' | 'models'>('tab')

// 「关于」：手动检查更新进行中标志 / 监听错误
const checking = ref(false)
const checkError = ref('')

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
      tab.value = 'tab'
      tokenInput.value = ''
      savedHint.value = ''
      checkError.value = ''
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

// 手动检查更新：触发后轮询 /api/settings 拿后台结果（超时 6s 提示稍后再看）
async function runCheck() {
  if (checking.value) return
  checking.value = true
  checkError.value = ''
  try {
    await checkAppUpdate()
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 500))
      const fresh = await fetchSettings()
      if (fresh.app_update.status !== 'none' && fresh.app_update.checked_at
        && fresh.app_update.checked_at !== info.value?.app_update.checked_at) {
        info.value = fresh
        checking.value = false
        return
      }
      info.value = fresh
    }
    // 超时：后台检查未完成（网络慢），提示稍后重开弹窗查看
    checkError.value = '网络较慢，稍后重新打开设置查看结果'
  } catch (e) {
    checkError.value = e instanceof Error ? e.message : '检查更新失败'
  } finally {
    checking.value = false
  }
}

/** 「关于」检查更新行的状态文案与样式 */
const updateLine = computed(() => {
  const u = info.value?.app_update
  if (checking.value) return { text: '检查中…', cls: 'dim' }
  if (!u || u.status === 'none') return { text: '尚未检查', cls: 'dim' }
  if (u.status === 'up_to_date') return { text: `已是最新（${u.latest_tag || `v${info.value?.version}`}）`, cls: 'ok' }
  if (u.status === 'newer') return { text: `发现新版本 ${u.latest_tag}`, cls: 'upd' }
  return { text: '暂无法检查更新', cls: 'dim' }
})

const repoHost = computed(() => {
  try { return new URL(info.value?.repo_url ?? '').hostname + new URL(info.value?.repo_url ?? '').pathname } catch { return info.value?.repo_url ?? '' }
})

// ---- 模型中转站推荐（独立选项卡）：点击链接直接跳转（桌面壳走系统浏览器，网页开新标签） ----
interface ModelSourceLink {
  label: string
  signupUrl: string
}
const MODEL_SOURCE_LINKS: ModelSourceLink[] = [
  { label: '芯谷 AI（国内模型）', signupUrl: 'https://api.xingu-ai.com/sign-up?aff=iC5W' },
  { label: '芯谷 AI（国外模型）', signupUrl: 'https://www.xinguhub.com/sign-up?aff=lyKd' },
]

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
      <template v-else>
        <div class="settings-tabs" role="tablist">
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'tab' }"
            :aria-selected="tab === 'tab'"
            @click="tab = 'tab'"
          >配置</button>
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'about' }"
            :aria-selected="tab === 'about'"
            @click="tab = 'about'"
          >关于</button>
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'models' }"
            :aria-selected="tab === 'models'"
            @click="tab = 'models'"
          >模型中转站</button>
        </div>

        <div v-show="tab === 'tab'" class="settings-body">
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

        <div v-show="tab === 'about'" class="settings-body">
          <!-- 关于 -->
          <div class="settings-group" v-if="info">
            <div class="settings-title"><span>关于</span></div>
            <div class="settings-about-row">
              <span class="about-label">当前版本</span>
              <span class="about-val mono">v{{ info.version }}</span>
            </div>
            <div class="settings-about-row">
              <span class="about-label">检查更新</span>
              <span class="about-val" :class="updateLine.cls">{{ updateLine.text }}</span>
              <button class="btn small" :disabled="checking" @click="runCheck">
                {{ checking ? '检查中…' : '立即检查' }}
              </button>
            </div>
            <a
              v-if="updateLine.cls === 'upd'"
              class="settings-link"
              :href="info.repo_url + '/releases/latest'"
              target="_blank"
              rel="noopener"
              @click.prevent="openExternal(info.repo_url + '/releases/latest')"
            >前往下载新版本 ↗</a>
            <div class="settings-about-row">
              <span class="about-label">项目地址</span>
              <a
                class="settings-link mono"
                :href="info.repo_url"
                target="_blank"
                rel="noopener"
                @click.prevent="openExternal(info.repo_url)"
              >{{ repoHost }}</a>
            </div>
            <div v-if="checkError" class="settings-hint">{{ checkError }}</div>
            <p class="settings-desc">应用每 6 小时自动检查一次新版本；仓库地址可随时查看源码与发布说明。</p>
          </div>
        </div>

        <div v-show="tab === 'models'" class="settings-body">
          <!-- 模型中转站推荐：点击直接跳转注册/开通页 -->
          <div class="settings-group">
            <div class="settings-title"><span>模型中转站推荐</span></div>
            <div class="model-source-list">
              <a
                v-for="src in MODEL_SOURCE_LINKS"
                :key="src.label"
                class="model-source-row"
                :href="src.signupUrl"
                target="_blank"
                rel="noopener noreferrer"
                @click.prevent="openExternal(src.signupUrl)"
              >
                <span class="model-source-name">{{ src.label }}</span>
                <span class="model-source-open">前往开通 ↗</span>
              </a>
            </div>
            <p class="settings-desc">中转站用于获取大模型 API 密钥与接口地址（OpenAI 兼容格式）；列表仅供参考，请自行判断平台可靠性。</p>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
