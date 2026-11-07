<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { Project } from '../api'
import { addFavorite, bareFullName, removeFavorite, formatDate, formatStars, projectSource, SOURCE_LABELS } from '../api'
import { t, mapReqLabel } from '../composables/useI18n'
import DownloadPicker from './DownloadPicker.vue'
import InstallPromptModal from './InstallPromptModal.vue'

const props = defineProps<{ project: Project }>()
const router = useRouter()
const showPicker = ref(false)
const showPrompt = ref(false)

/** 来源平台：GitHub 项目不显示徽标（存量 1900+ 项目全是 GitHub，标了是噪音） */
const source = computed(() => projectSource(props.project))
const showSourceBadge = computed(() => source.value !== 'github')
const sourceLabel = computed(() => SOURCE_LABELS[source.value] ?? source.value)

// 卡片快速收藏：初始状态取项目 is_favorite，默认进「默认分组」
const fav = ref(props.project.is_favorite)
const favBusy = ref(false)

/** 收藏在别处变更（每日精选条 / 收藏弹窗）时同步本卡片。
 *  同一项目会同时出现在精选条和列表首屏，两处必须联动，
 *  否则一处点了收藏、另一处仍显示未收藏（实测踩到）。
 */
function onFavoriteChanged(e: Event) {
  const { id, favorite } = (e as CustomEvent<{ id: number; favorite: boolean }>).detail
  if (id === props.project.id) fav.value = favorite
}
onMounted(() => window.addEventListener('starpick:favorite-changed', onFavoriteChanged))
onUnmounted(() => window.removeEventListener('starpick:favorite-changed', onFavoriteChanged))

async function toggleFavorite() {
  if (favBusy.value) return
  favBusy.value = true
  try {
    if (fav.value) {
      await removeFavorite(props.project.id)
      fav.value = false
    } else {
      await addFavorite(props.project.id)
      fav.value = true
    }
    // 广播收藏变更：本卡片与每日精选条是同一项目的两个入口，
    // 不通知的话另一处仍显示旧状态。
    window.dispatchEvent(new CustomEvent('starpick:favorite-changed', {
      detail: { id: props.project.id, favorite: fav.value },
    }))
  } catch { /* 忽略：状态不变即反馈失败 */ } finally {
    favBusy.value = false
  }
}

const categoryColor = computed(() => {
  const map: Record<string, string> = {
    影视: '#f472b6',
    视频工具: '#e879f9',
    游戏: '#a78bfa',
    游戏开发: '#818cf8',
    开发: '#38bdf8',
    AI工具: '#34d399',
    效率工具: '#fbbf24',
    加速工具: '#fb923c',
    设计: '#fb7185',
    创作: '#f43f5e',
    音乐: '#2dd4bf',
    阅读: '#22d3ee',
  }
  return map[props.project.category] ?? '#9aa2b1'
})

/** 附加分类：categories 中除主分类外的标签（跨界项目多分类标注） */
const extraCategories = computed(() => {
  const cats = props.project.categories ?? []
  return cats.filter((c) => c !== props.project.category)
})

/** 整卡点击进详情（卡片内按钮均已 @click.stop 阻止冒泡） */
function openDetail() {
  router.push(`/project/${props.project.id}`)
}

/** 安装包还没探测到（后台逐个查 release，无 Token 限速 60 个/小时） */
const probing = computed(() => props.project.assets.length === 0 && !props.project.assets_probed)

/** 点「检测中」标签 → 打开采集中心看探测进度（AppHeader 监听同一全局事件） */
function openCollect() {
  window.dispatchEvent(new CustomEvent('starpick:open-collect'))
}

/** 有安装包 → 弹出版本选择；无安装包 → 卡片按钮改走安装提示词（见 openInstallPrompt） */
function download() {
  if (props.project.assets.length > 0) {
    showPicker.value = true
  }
}

/** 预览安装提示词：复制给豆包等能操作电脑的 AI 助手，由它帮用户在本机安装 */
function openInstallPrompt() {
  showPrompt.value = true
}
</script>

<template>
  <article class="card card-clickable" @click="openDetail">
    <div class="card-head">
      <div>
        <div class="card-title">
          <span class="title-link">{{ bareFullName(project) }}</span>
          <!-- 来源徽标：仅非 GitHub 显示（存量项目全是 GitHub，标了只是噪音） -->
          <span
            v-if="showSourceBadge"
            class="source-badge"
            :title="t('card_source_tip').replace('{s}', sourceLabel)"
          >{{ sourceLabel }}</span>
          <span v-if="project.is_new" class="new-badge" :title="t('card_new_tip')">NEW</span>
        </div>
        <div class="card-owner">@{{ project.owner }}</div>
      </div>
      <div class="card-head-right">
        <span class="stars">★ {{ formatStars(project.stars) }}</span>
        <!-- 卡片快速收藏：点击直接收藏/取消（阻止冒泡，不触发进详情）。
             图标是书签而非星星——它紧挨着 `★ 398k` 星数，两个星星会让人
             分不清哪个可点。与每日精选条共用同一套 .fav-btn 样式。 -->
        <button
          class="fav-btn"
          :class="{ on: fav }"
          :disabled="favBusy"
          :title="fav ? t('card_fav_remove_tip') : t('card_fav_tip')"
          :aria-label="fav ? t('card_fav_remove_tip') : t('card_fav_tip')"
          @click.stop="toggleFavorite"
        >
          <svg class="fav-btn-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
          </svg>
        </button>
      </div>
    </div>

    <div class="card-desc">
      {{ project.description || t('card_no_desc') }}
    </div>

    <div class="meta-row">
      <span class="tag" :style="{ color: categoryColor, borderColor: categoryColor + '55' }">
        {{ t('cat_' + project.category) }}
      </span>
      <span v-for="c in extraCategories" :key="c" class="tag extra-cat" :title="t('detail_extra_cat_tip').replace('{c}', t('cat_' + c))">
        {{ t('cat_' + c) }}
      </span>
      <span v-if="project.lang" class="tag" :class="project.lang === 'zh' ? 'lang-zh' : 'lang-en'">
        {{ project.lang === 'zh' ? t('filter_zh') : t('filter_en') }}
      </span>
      <span v-if="project.language" class="tag lang">{{ project.language }}</span>
      <span v-if="project.zh_friendly" class="tag zh" :title="t('card_zh_friendly_tip')">{{ t('card_zh_friendly') }}</span>
      <span v-for="p in project.platforms" :key="p" class="tag platform">{{ p }}</span>
      <span class="tag">{{ t('card_update').replace('{date}', formatDate(project.pushed_at)) }}</span>
    </div>

    <div v-if="project.system_requirements.length" class="meta-row">
      <span v-for="r in project.system_requirements" :key="r" class="tag req" :title="mapReqLabel(r)">{{ mapReqLabel(r) }}</span>
    </div>

    <div class="card-foot">
      <template v-if="probing">
        <button
          class="probe-chip"
          :title="t('card_detecting_tip')"
          @click.stop="openCollect"
        >{{ t('card_detecting') }}</button>
        <button
          class="btn prompt"
          :title="t('card_prompt_tip')"
          @click.stop="openInstallPrompt"
        >{{ t('card_copy_prompt') }}</button>
      </template>
      <template v-else-if="project.assets.length === 0">
        <button
          class="btn dl"
          :title="t('card_prompt_tip')"
          @click.stop="openInstallPrompt"
        >{{ t('card_copy_prompt') }}</button>
      </template>
      <template v-else>
        <button class="btn dl" @click.stop="download">
          {{ t('card_download').replace('{n}', String(project.assets.length)) }}
        </button>
        <button
          class="btn prompt"
          :title="t('card_prompt_tip')"
          @click.stop="openInstallPrompt"
        >{{ t('card_copy_prompt') }}</button>
      </template>
    </div>
  </article>

  <DownloadPicker v-if="showPicker" :project="project" @close="showPicker = false" />
  <InstallPromptModal v-if="showPrompt" :project="project" @close="showPrompt = false" />
</template>
