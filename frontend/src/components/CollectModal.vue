<script setup lang="ts">
// 采集中心弹窗：集中管理采集与更新检查（独立页面内容单薄，改为全局弹窗）
import { computed, ref, watch } from 'vue'
import {
  fetchCollectStatus,
  triggerRun,
  fetchCollectParams,
  fetchCategories,
  fetchUpdateStatus,
  triggerUpdates,
  fetchHealth,
  type CollectParams,
  type CollectStatus,
  type UpdateStatus,
} from '../api'
import { t } from '../composables/useI18n'
import RoundsModal from './RoundsModal.vue'
import UpdatesModal from './UpdatesModal.vue'
import BaseSelect from './BaseSelect.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'openSettings'): void }>()

const collectStatus = ref<CollectStatus | null>(null)
const running = ref(false)
const toast = ref('')
const updateStatus = ref<UpdateStatus | null>(null)
const updateRunning = ref(false)

// 历史子弹窗（内嵌，不再跳转路由）
const roundsOpen = ref(false)
const updatesOpen = ref(false)

const collectParams = ref<CollectParams | null>(null)
const minStars = ref(200)
const maxInactiveDays = ref(90)
const perCatTop = ref<number>(0)   // 0=用后端默认（100）

// GitHub Token 配置状态：true=已配置（采集快）；false=匿名限流（采集慢）；null=未知
const hasToken = ref<boolean | null>(null)
watch(
  () => props.open,
  (open) => {
    if (open) {
      fetchHealth()
        .then((h) => (hasToken.value = !!h.has_token))
        .catch(() => (hasToken.value = null))
    }
  },
  { immediate: true },
)

// 分类选择：采集支持「全部 / 多选指定分类（可多个一起采集）/ 自定义分类」；更新支持「全部 / 指定分类」
// collectCats / updateCats：空数组=全部；否则为选中的分类名集合（多选）
const allCategories = ref<string[]>([])
const collectCats = ref<string[]>([])
const customMode = ref(false)
const customCatName = ref('')
const customCatKeywords = ref('')
const updateCats = ref<string[]>([])

function toggleCat(c: string) {
  const i = collectCats.value.indexOf(c)
  if (i >= 0) collectCats.value.splice(i, 1)
  else collectCats.value.push(c)
}
function resetCats() {
  collectCats.value = []
}
function toggleUpdateCat(c: string) {
  const i = updateCats.value.indexOf(c)
  if (i >= 0) updateCats.value.splice(i, 1)
  else updateCats.value.push(c)
}
function resetUpdateCats() {
  updateCats.value = []
}
function toggleCustom() {
  customMode.value = !customMode.value
}
const STAR_OPTIONS = [
  { label: () => t('collect_limit_none'), value: 0 },
  { label: () => '≥ 50', value: 50 },
  { label: () => '≥ 100', value: 100 },
  { label: () => '≥ 200', value: 200 },
  { label: () => '≥ 500', value: 500 },
  { label: () => '≥ 1000', value: 1000 },
]
const ACTIVE_OPTIONS = [
  { label: () => t('collect_limit_30d'), value: 30 },
  { label: () => t('collect_limit_60d'), value: 60 },
  { label: () => t('collect_limit_90d'), value: 90 },
  { label: () => t('collect_limit_180d'), value: 180 },
  { label: () => t('collect_limit_unlimited'), value: 0 },
]
// 每类收录上限：第一项显示后端默认值（配置可调）
const PER_CAT_OPTIONS = computed(() => [
  { label: () => t('collect_limit_default').replace('{n}', String(collectParams.value?.per_category_max ?? 100)), value: 0 },
  { label: () => t('collect_limit_50'), value: 50 },
  { label: () => t('collect_limit_100'), value: 100 },
  { label: () => t('collect_limit_200'), value: 200 },
  { label: () => t('collect_limit_500'), value: 500 },
])

let pollTimer: ReturnType<typeof setInterval> | undefined
let updatePollTimer: ReturnType<typeof setInterval> | undefined

const PHASE_LABEL: Record<string, () => string> = {
  searching: () => t('collect_phase_searching'),
  enriching: () => t('collect_phase_enriching'),
  storing: () => t('collect_phase_storing'),
  backfilling: () => t('collect_phase_backfilling'),
  finished: () => t('collect_phase_finished'),
}
const phaseLabel = computed(() => { const f = PHASE_LABEL[collectStatus.value?.phase ?? '']; return f ? f() : '' })
const runningNow = computed(() => !!collectStatus.value?.running)

function pct(done: number, totalN: number): string {
  if (!totalN) return '0%'
  return `${Math.min(100, Math.round((done / totalN) * 100))}%`
}

function backfillPending(s: CollectStatus): boolean {
  return s.backfill_total > 0 && s.backfill_done < s.backfill_total
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

async function pollOnce(): Promise<boolean> {
  try {
    const s = await fetchCollectStatus()
    collectStatus.value = s
    if (!s.running && !backfillPending(s)) {
      stopPolling()
      return false
    }
    return true
  } catch {
    stopPolling()
    return false
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    const keep = await pollOnce()
    if (!keep) stopPolling()
  }, 3000)
  pollOnce()
}

async function runNow() {
  if (running.value) return
  running.value = true
  try {
    const payload: {
      min_stars?: number
      max_inactive_days?: number
      per_category_top?: number
      categories?: string[]
      custom_name?: string
      custom_keywords?: string
    } = {
      min_stars: minStars.value,
      max_inactive_days: maxInactiveDays.value,
    }
    if (perCatTop.value > 0) {
      payload.per_category_top = perCatTop.value
    }
    if (collectCats.value.length) {
      payload.categories = [...collectCats.value]
    }
    if (
      customMode.value &&
      customCatName.value.trim() &&
      customCatKeywords.value.trim()
    ) {
      payload.custom_name = customCatName.value.trim()
      payload.custom_keywords = customCatKeywords.value.trim()
    }
    const res = await triggerRun(payload)
    toast.value = res.reason || t('collect_triggered')
    setTimeout(() => (toast.value = ''), 5000)
    if (res.status === 'accepted' || res.status === 'running') {
      startPolling()
    }
  } catch (e) {
    toast.value = e instanceof Error ? e.message : t('collect_trigger_fail')
    setTimeout(() => (toast.value = ''), 5000)
  } finally {
    running.value = false
  }
}

function updatePct(done: number, totalN: number): string {
  if (!totalN) return '0%'
  return `${Math.min(100, Math.round((done / totalN) * 100))}%`
}

function stopUpdatePolling() {
  if (updatePollTimer) {
    clearInterval(updatePollTimer)
    updatePollTimer = undefined
  }
}

async function updatePollOnce(): Promise<boolean> {
  try {
    const s = await fetchUpdateStatus()
    updateStatus.value = s
    if (!s.running) {
      stopUpdatePolling()
      return false
    }
    return true
  } catch {
    stopUpdatePolling()
    return false
  }
}

function startUpdatePolling() {
  stopUpdatePolling()
  updatePollTimer = setInterval(async () => {
    const keep = await updatePollOnce()
    if (!keep) stopUpdatePolling()
  }, 3000)
  updatePollOnce()
}

async function runUpdates() {
  if (updateRunning.value) return
  updateRunning.value = true
  try {
    const cats = updateCats.value.length ? [...updateCats.value] : undefined
    const res = await triggerUpdates(cats)
    toast.value = res.reason || t('collect_update_triggered')
    setTimeout(() => (toast.value = ''), 5000)
    if (res.status === 'started') {
      startUpdatePolling()
    }
  } catch (e) {
    toast.value = e instanceof Error ? e.message : t('collect_trigger_fail')
    setTimeout(() => (toast.value = ''), 5000)
  } finally {
    updateRunning.value = false
  }
}

// 打开弹窗：加载参数 + 恢复进行中的进度；关闭：停轮询
watch(
  () => props.open,
  (open) => {
    if (open) {
      fetchCategories().then((r) => {
        allCategories.value = r.categories ?? []
      }).catch(() => { /* 后端暂不可用时保持空列表 */ })
      fetchCollectParams().then((p) => {
        collectParams.value = p
        minStars.value = p.min_stars
        maxInactiveDays.value = p.max_inactive_days
        if (p.categories && p.categories.length && allCategories.value.length === 0) {
          allCategories.value = p.categories
        }
      }).catch(() => { /* 保留默认值 */ })
      pollOnce().then((keep) => {
        if (keep) startPolling()
      })
      updatePollOnce().then((keep) => {
        if (keep) startUpdatePolling()
      })
    } else {
      stopPolling()
      stopUpdatePolling()
    }
  },
)

function close() {
  emit('close')
}

// 更新检查是否展示面板：进行中 或 本次已有结果（结束后保留结果摘要，不直接消失）
const updatePanelVisible = computed(() => {
  const s = updateStatus.value
  if (!s) return false
  return s.running || s.done > 0
})

function fmtTimeShort(iso: string): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ''
  }
}
</script>

<template>
  <div v-if="open" class="modal-overlay" @click.self="close">
    <div class="collect-modal">
      <div class="modal-head">
        <h2>{{ t('collect_title') }}</h2>
        <button class="modal-close" :title="t('collect_close')" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <!-- 采集控制 -->
      <div class="collect-ctrl">
        <button class="btn" :disabled="running || runningNow" @click="runNow">
          {{ running ? t('collect_triggering') : runningNow ? t('collect_running') : t('collect_start') }}
        </button>
        <!-- Token 状态提示：无 Token 引导配置，有 Token 显示预计耗时（数值来自实际轮次统计） -->
        <span v-if="hasToken" class="collect-hint">{{ t('collect_has_token') }}</span>
        <span v-else class="collect-hint collect-hint-warn">
          {{ t('collect_no_token') }}
          <button class="link-btn" @click="emit('openSettings')">{{ t('collect_go_config') }}</button>
        </span>
      </div>

      <!-- 采集参数（本轮过滤条件，可手动选择覆盖默认值） -->
      <div class="collect-params">
        <div class="param-item">
          <span class="param-label">{{ t('collect_star') }}</span>
          <BaseSelect v-model="minStars" :options="STAR_OPTIONS" />
        </div>
        <div class="param-item">
          <span class="param-label">{{ t('collect_activity') }}</span>
          <BaseSelect v-model="maxInactiveDays" :options="ACTIVE_OPTIONS" />
        </div>
        <div class="param-item">
          <span class="param-label">{{ t('collect_per_cat') }}</span>
          <BaseSelect v-model="perCatTop" :options="PER_CAT_OPTIONS" />
        </div>
        <div class="param-item cat-multi">
          <span class="param-label">{{ t('collect_categories') }}</span>
          <div class="cat-chips">
            <button class="chip" :class="{ active: collectCats.length === 0 }" @click="resetCats">全部</button>
            <button
              v-for="c in allCategories"
              :key="c"
              class="chip"
              :class="{ active: collectCats.includes(c) }"
              @click="toggleCat(c)"
            >{{ c }}</button>
            <button
              class="chip custom-chip"
              :class="{ active: customMode }"
              @click="toggleCustom"
            >{{ t('collect_custom') }}</button>
          </div>
        </div>
        <div v-if="customMode" class="param-custom">
          <label class="param-item">
            <span class="param-label">{{ t('collect_cat_name') }}</span>
            <input v-model="customCatName" :placeholder="t('collect_cat_name_ph')" />
          </label>
          <label class="param-item">
            <span class="param-label">{{ t('collect_keywords') }}</span>
            <input v-model="customCatKeywords" :placeholder="t('collect_keywords_ph')" />
          </label>
          <span class="param-hint">{{ t('collect_custom_hint') }}</span>
        </div>
        <span class="param-hint">{{ t('collect_cats_hint').replace('{n}', String(collectParams?.per_category_max ?? '—')) }}</span>
      </div>

      <!-- 采集进度面板（仅进行中或后台回填未完成时展示） -->
      <div v-if="collectStatus && (collectStatus.running || collectStatus.backfill_total > 0)" class="collect-panel" :class="{ done: !runningNow }">
        <div class="cp-head">
          <span class="cp-phase">{{ phaseLabel }}</span>
          <span class="cp-msg">{{ collectStatus.message }}</span>
        </div>
        <div class="cp-bar">
          <div
            v-if="collectStatus.phase === 'enriching' || collectStatus.enriched_total > 0"
            class="cp-bar-fill"
            :style="{ width: pct(collectStatus.enriched_done, collectStatus.enriched_total) }"
          ></div>
          <div
            v-else
            class="cp-bar-fill"
            :style="{ width: pct(collectStatus.categories_done, collectStatus.categories_total) }"
          ></div>
        </div>
        <div class="cp-meta">
          <span v-if="collectStatus.categories_total > 0">{{ t('collect_cat_progress').replace('{a}', String(collectStatus.categories_done)).replace('{b}', String(collectStatus.categories_total)) }}</span>
          <span v-if="collectStatus.enriched_total > 0">{{ t('collect_detail_progress').replace('{a}', String(collectStatus.enriched_done)).replace('{b}', String(collectStatus.enriched_total)) }}</span>
          <span v-if="collectStatus.backfill_total > 0" :class="{ pending: !collectStatus.running && collectStatus.backfill_done < collectStatus.backfill_total }">
            {{ t('collect_pkg_progress').replace('{a}', String(collectStatus.backfill_done)).replace('{b}', String(collectStatus.backfill_total)) }}{{ !collectStatus.running && collectStatus.backfill_done < collectStatus.backfill_total ? '（后台继续）' : '' }}
          </span>
          <span v-if="!runningNow" class="cp-finished">{{ collectStatus.message }}</span>
        </div>
      </div>

      <!-- 更新检查控制 -->
      <div class="collect-ctrl update-ctrl">
        <button class="btn" :disabled="updateRunning || !!updateStatus?.running" @click="runUpdates">
          {{ updateRunning ? t('collect_triggering') : updateStatus?.running ? t('collect_update_checking') : t('collect_update_check') }}
        </button>
        <div class="param-item cat-multi">
          <span class="param-label">{{ t('collect_update_check_cats') }}</span>
          <div class="cat-chips">
            <button class="chip" :class="{ active: updateCats.length === 0 }" @click="resetUpdateCats">全部</button>
            <button
              v-for="c in allCategories"
              :key="c"
              class="chip"
              :class="{ active: updateCats.includes(c) }"
              @click="toggleUpdateCat(c)"
            >{{ c }}</button>
          </div>
        </div>
        <span class="collect-hint">{{ t('collect_update_hint') }}</span>
      </div>

      <!-- 更新检查进度面板：进行中显示进度；结束后保留本次结果摘要 -->
      <div v-if="updatePanelVisible" class="collect-panel update-panel" :class="{ done: !updateStatus?.running }">
        <div class="cp-head">
          <span class="cp-phase">{{ updateStatus?.running ? t('collect_update_phase') : t('collect_update_done') }}</span>
          <span class="cp-msg">{{ updateStatus?.running ? updateStatus.message : '本次更新检查已结束' }}</span>
        </div>
        <div v-if="updateStatus?.running" class="cp-bar">
          <div class="cp-bar-fill" :style="{ width: updatePct(updateStatus.done, updateStatus.total) }"></div>
        </div>
        <div class="cp-meta">
          <span v-if="updateStatus?.running">{{ t('collect_update_progress').replace('{a}', String(updateStatus.done)).replace('{b}', String(updateStatus.total)) }}</span>
          <span class="strong">{{ t('collect_update_found').replace('{n}', String(updateStatus?.updated)) }}</span>
          <span>{{ t('collect_update_nochange').replace('{n}', String(updateStatus?.nochange)) }}</span>
          <span v-if="(updateStatus?.failed ?? 0) > 0" class="err-text">{{ t('collect_update_failed').replace('{n}', String(updateStatus?.failed)) }}</span>
          <span v-if="!updateStatus?.running && updateStatus?.updated_at" class="cp-time">完成 {{ fmtTimeShort(updateStatus?.updated_at ?? '') }}</span>
        </div>
      </div>

      <!-- 历史入口：打开内嵌子弹窗，不再跳转路由 -->
      <div class="modal-history">
        <button class="btn ghost small" @click="roundsOpen = true">{{ t('collect_rounds_hist') }}</button>
        <button class="btn ghost small" @click="updatesOpen = true">{{ t('collect_updates_hist') }}</button>
      </div>

      <div v-if="toast" class="toast">{{ toast }}</div>
    </div>

    <!-- 内嵌历史子弹窗（盖在采集中心之上） -->
    <RoundsModal :open="roundsOpen" @close="roundsOpen = false" />
    <UpdatesModal :open="updatesOpen" @close="updatesOpen = false" />
  </div>
</template>
