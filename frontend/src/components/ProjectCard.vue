<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { Project } from '../api'
import { addFavorite, removeFavorite, formatDate, formatStars } from '../api'
import DownloadPicker from './DownloadPicker.vue'
import InstallPromptModal from './InstallPromptModal.vue'

const props = defineProps<{ project: Project }>()
const router = useRouter()
const showPicker = ref(false)
const showPrompt = ref(false)

// 卡片快速收藏：初始状态取项目 is_favorite，默认进「默认分组」
const fav = ref(props.project.is_favorite)
const favBusy = ref(false)

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
          <span class="title-link">{{ project.full_name }}</span>
          <span v-if="project.is_new" class="new-badge" title="最近一轮采集新增">NEW</span>
        </div>
        <div class="card-owner">@{{ project.owner }}</div>
      </div>
      <div class="card-head-right">
        <span class="stars">★ {{ formatStars(project.stars) }}</span>
        <!-- 卡片快速收藏：点击直接收藏/取消（阻止冒泡，不触发进详情） -->
        <button
          class="fav-star"
          :class="{ on: fav }"
          :title="fav ? '取消收藏' : '收藏到默认分组'"
          @click.stop="toggleFavorite"
        >
          <svg class="fav-star-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2l2.9 6.6 7.1.6-5.4 4.7 1.6 7-6.2-3.7-6.2 3.7 1.6-7-5.4-4.7 7.1-.6z" />
          </svg>
        </button>
      </div>
    </div>

    <div class="card-desc">
      {{ project.description || '（无简介）' }}
    </div>

    <div class="meta-row">
      <span class="tag" :style="{ color: categoryColor, borderColor: categoryColor + '55' }">
        {{ project.category }}
      </span>
      <span v-for="c in extraCategories" :key="c" class="tag extra-cat" :title="`跨界项目，同时属于「${c}」`">
        {{ c }}
      </span>
      <span v-if="project.lang" class="tag" :class="project.lang === 'zh' ? 'lang-zh' : 'lang-en'">
        {{ project.lang === 'zh' ? '中文' : '英文' }}
      </span>
      <span v-if="project.language" class="tag lang">{{ project.language }}</span>
      <span v-if="project.zh_friendly" class="tag zh" title="官方提供中文文档或说明（官网/仓库），对中文用户友好">中文友好</span>
      <span v-for="p in project.platforms" :key="p" class="tag platform">{{ p }}</span>
      <span class="tag">更新 {{ formatDate(project.pushed_at) }}</span>
    </div>

    <div v-if="project.system_requirements.length" class="meta-row">
      <span v-for="r in project.system_requirements" :key="r" class="tag req" :title="r">{{ r }}</span>
    </div>

    <div class="card-foot">
      <template v-if="probing">
        <button
          class="probe-chip"
          title="正在后台逐个探测 GitHub release（无 Token 时限速约 60 个/小时），完成后这里会出现「下载版本」按钮。点击查看探测进度"
          @click.stop="openCollect"
        >安装包检测中…</button>
        <button
          class="btn prompt"
          title="复制安装提示词，发给豆包等能操作电脑的 AI 助手，让它帮你安装"
          @click.stop="openInstallPrompt"
        >复制安装提示词</button>
      </template>
      <template v-else-if="project.assets.length === 0">
        <button
          class="btn dl"
          title="复制安装提示词，发给豆包等能操作电脑的 AI 助手，让它帮你安装"
          @click.stop="openInstallPrompt"
        >复制安装提示词</button>
      </template>
      <template v-else>
        <button class="btn dl" @click.stop="download">
          下载版本 · {{ project.assets.length }} 个
        </button>
        <button
          class="btn prompt"
          title="复制安装提示词，发给豆包等能操作电脑的 AI 助手，让它帮你安装"
          @click.stop="openInstallPrompt"
        >复制安装提示词</button>
      </template>
    </div>
  </article>

  <DownloadPicker v-if="showPicker" :project="project" @close="showPicker = false" />
  <InstallPromptModal v-if="showPrompt" :project="project" @close="showPrompt = false" />
</template>
