<script setup lang="ts">
// 设置弹窗：GitHub Token 配置（保存后热生效）+「关于」（版本 / 检查更新 / 一键更新）+「模型中转站」
import { computed, ref, watch } from 'vue'
import {
  fetchSettings, saveToken, clearToken, checkAppUpdate, fetchAppUpdateDownload, openExternal,
  type SettingsState,
} from '../api'
import { t as i18nT } from '../composables/useI18n'

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
    error.value = e instanceof Error ? e.message : i18nT('settings_read_fail')
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
    savedHint.value = i18nT('settings_saved')
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18nT('settings_save_fail')
  } finally {
    saving.value = false
  }
}

async function remove() {
  saving.value = true
  error.value = ''
  try {
    info.value = await clearToken()
    savedHint.value = i18nT('settings_cleared')
  } catch (e) {
    error.value = e instanceof Error ? e.message : i18nT('settings_clear_fail')
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
    checkError.value = i18nT('settings_check_slow')
  } catch (e) {
    checkError.value = e instanceof Error ? e.message : i18nT('settings_check_fail')
  } finally {
    checking.value = false
  }
}

/** 「关于」检查更新行的状态文案与样式 */
const updateLine = computed(() => {
  const u = info.value?.app_update
  if (checking.value) return { text: i18nT('settings_checking'), cls: 'dim' }
  if (!u || u.status === 'none') return { text: i18nT('settings_not_checked'), cls: 'dim' }
  if (u.status === 'up_to_date') return { text: i18nT('settings_up_to_date').replace('{tag}', u.latest_tag || `v${info.value?.version}`), cls: 'ok' }
  if (u.status === 'newer') return { text: i18nT('settings_newer').replace('{tag}', u.latest_tag), cls: 'upd' }
  return { text: i18nT('settings_check_unknown'), cls: 'dim' }
})

const repoHost = computed(() => {
  try { return new URL(info.value?.repo_url ?? '').hostname + new URL(info.value?.repo_url ?? '').pathname } catch { return info.value?.repo_url ?? '' }
})

/** 一键更新（仅 Windows 桌面壳）：取直链 → 调桌面壳桥下载并静默安装 → 进程退出 */
const updating = ref(false)
const updateError = ref('')
async function oneClickUpdate() {
  if (updating.value) return
  updating.value = true
  updateError.value = ''
  try {
    const r = await fetchAppUpdateDownload()
    const bridge = (window as { pywebview?: { api?: { update_and_exit?: (u: string, n: string) => string } } })
      .pywebview?.api
    if (!bridge?.update_and_exit) {
      updateError.value = i18nT('settings_not_desktop')
      return
    }
    const err = bridge.update_and_exit(r.download_url, r.file_name)
    if (err) updateError.value = err
    // 成功后桌面壳自行退出，无需处理
  } catch (e) {
    updateError.value = e instanceof Error ? e.message : i18nT('settings_update_fail')
  } finally {
    updating.value = false
  }
}

// ---- 模型中转站推荐（独立选项卡）：点击链接直接跳转（桌面壳走系统浏览器，网页开新标签） ----
interface ModelSourceLink {
  label: string
  signupUrl: string
}
// computed：语言切换时重新求值（常量会在模块加载时定死语言）
const MODEL_SOURCE_LINKS = computed<ModelSourceLink[]>(() => [
  { label: i18nT('settings_model_domestic'), signupUrl: 'https://api.xingu-ai.com/sign-up?aff=iC5W' },
  { label: i18nT('settings_model_global'), signupUrl: 'https://www.xinguhub.com/sign-up?aff=lyKd' },
])

function close() {
  emit('close')
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click.self="close">
    <div class="modal">
      <div class="modal-head">
        <h2>{{ i18nT('settings_title') }}</h2>
        <button class="modal-close" :title="i18nT('settings_close')" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">{{ i18nT('settings_loading') }}</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <template v-else>
        <div class="settings-tabs" role="tablist">
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'tab' }"
            :aria-selected="tab === 'tab'"
            @click="tab = 'tab'"
          >{{ i18nT('settings_tab_config') }}</button>
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'about' }"
            :aria-selected="tab === 'about'"
            @click="tab = 'about'"
          >{{ i18nT('settings_tab_about') }}</button>
          <button
            class="settings-tab" role="tab"
            :class="{ active: tab === 'models' }"
            :aria-selected="tab === 'models'"
            @click="tab = 'models'"
          >{{ i18nT('settings_tab_models') }}</button>
        </div>

        <div v-show="tab === 'tab'" class="settings-body">
          <!-- GitHub Token 配置 -->
          <div class="settings-group">
            <div class="settings-title">
              <span>{{ i18nT('settings_token_title') }}</span>
              <span class="tag" :class="{ upd: info?.has_token }">
                {{ info?.has_token ? i18nT('settings_token_configured') : i18nT('settings_token_none') }}
              </span>
            </div>
            <p class="settings-desc">{{ i18nT('settings_token_desc') }}</p>
            <div v-if="info?.has_token" class="settings-current">
              {{ i18nT('settings_token_current') }}：<code class="token-mask">{{ info.masked_token }}</code>
              <span class="dim">{{ i18nT('settings_token_masked') }}</span>
            </div>
            <div class="settings-row">
              <input
                v-model="tokenInput"
                class="settings-input"
                type="password"
                :placeholder="i18nT('settings_token_placeholder')"
                autocomplete="off"
                spellcheck="false"
                @keyup.enter="submit"
              />
              <button class="btn" :disabled="saving || !tokenInput.trim()" @click="submit">{{ i18nT('settings_token_save') }}</button>
              <button v-if="info?.has_token" class="btn ghost" :disabled="saving" @click="remove">{{ i18nT('settings_token_clear') }}</button>
            </div>
            <div v-if="savedHint" class="settings-hint ok-hint">{{ savedHint }}</div>
          </div>

          <!-- 限流状态 -->
          <div class="settings-group" v-if="info">
            <div class="settings-title"><span>{{ i18nT('settings_rate_title') }}</span></div>
            <div class="settings-rate">
              <div class="rate-item">
                <span class="rate-label">{{ i18nT('settings_rate_search') }}</span>
                <span class="rate-val">{{ i18nT('settings_rate_per_min').replace('{n}', String(info.search_rate_per_minute)) }}</span>
              </div>
              <div class="rate-item">
                <span class="rate-label">{{ i18nT('settings_rate_core') }}</span>
                <span class="rate-val">{{ i18nT('settings_rate_per_hour').replace('{n}', String(info.core_rate_per_hour)) }}</span>
              </div>
            </div>
            <p class="settings-desc">{{ i18nT('settings_rate_desc') }}</p>
          </div>
        </div>

        <div v-show="tab === 'about'" class="settings-body">
          <!-- 关于 -->
          <div class="settings-group" v-if="info">
            <div class="settings-title"><span>{{ i18nT('settings_tab_about') }}</span></div>
            <div class="settings-about-row">
              <span class="about-label">{{ i18nT('settings_about_cur_ver') }}</span>
              <span class="about-val mono">v{{ info.version }}</span>
            </div>
            <div class="settings-about-row">
              <span class="about-label">{{ i18nT('settings_about_check') }}</span>
              <span class="about-val" :class="updateLine.cls">{{ updateLine.text }}</span>
              <button class="btn small" :disabled="checking" @click="runCheck">
                {{ checking ? i18nT('settings_checking') : i18nT('settings_about_check_now') }}
              </button>
            </div>
            <div
              v-if="updateLine.cls === 'upd' && info.deployment === 'desktop'"
              class="settings-about-row"
            >
              <span class="about-label">{{ i18nT('settings_about_oneclick') }}</span>
              <button class="btn small" :disabled="updating" @click="oneClickUpdate">
                {{ updating ? i18nT('settings_about_downloading') : i18nT('settings_about_download') }}
              </button>
              <a
                class="settings-link"
                :href="info.repo_url + '/releases/latest'"
                target="_blank"
                rel="noopener"
                @click.prevent="openExternal(info.repo_url + '/releases/latest')"
              >{{ i18nT('settings_about_manual') }}</a>
            </div>
            <div v-if="updateError" class="settings-hint">{{ updateError }}</div>
            <div
              v-else-if="updateLine.cls === 'upd' && info.deployment === 'web'"
              class="settings-hint"
            >{{ i18nT('settings_about_web_hint') }}</div>
            <div class="settings-about-row">
              <span class="about-label">{{ i18nT('settings_about_repo') }}</span>
              <a
                class="settings-link mono"
                :href="info.repo_url"
                target="_blank"
                rel="noopener"
                @click.prevent="openExternal(info.repo_url)"
              >{{ repoHost }}</a>
            </div>
            <div v-if="checkError" class="settings-hint">{{ checkError }}</div>
            <p class="settings-desc">{{ i18nT('settings_about_desc') }}</p>
          </div>
        </div>

        <div v-show="tab === 'models'" class="settings-body">
          <!-- 模型中转站推荐：点击直接跳转注册/开通页 -->
          <div class="settings-group">
            <div class="settings-title"><span>{{ i18nT('settings_models_title') }}</span></div>
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
                <span class="model-source-open">{{ i18nT('settings_models_open') }}</span>
              </a>
            </div>
            <p class="settings-desc">{{ i18nT('settings_models_desc') }}</p>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
