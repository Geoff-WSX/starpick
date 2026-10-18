<script setup lang="ts">
// 收藏弹窗（原收藏页迁移；导航栏入口改弹窗模式）
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchFavorites,
  removeFavorite,
  formatStars,
  type FavoriteGroups,
  type FavoriteOut,
} from '../api'
import BaseSelect from './BaseSelect.vue'

const SORT_OPTIONS = [
  { label: '收藏时间', value: 'time' },
  { label: 'Star 最多', value: 'stars' },
]

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()
const router = useRouter()

const items = ref<FavoriteOut[]>([])
const groups = ref<FavoriteGroups[]>([])
const activeGroup = ref('')
const loading = ref(false)
const error = ref('')
const toast = ref('')
const searchQ = ref('')
const sortMode = ref<'time' | 'stars'>('time')

// 本地过滤 + 排序（收藏量有限，前端处理即可）
const filtered = computed(() => {
  let list = items.value
  const q = searchQ.value.trim().toLowerCase()
  if (q) {
    list = list.filter((i) =>
      `${i.name} ${i.full_name} ${i.description || ''}`.toLowerCase().includes(q),
    )
  }
  if (sortMode.value === 'stars') {
    list = [...list].sort((a, b) => b.stars - a.stars)
  } else {
    list = [...list].sort((a, b) => b.favorited_at.localeCompare(a.favorited_at))
  }
  return list
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await fetchFavorites(activeGroup.value)
    items.value = data.items
    groups.value = data.groups
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载收藏失败'
  } finally {
    loading.value = false
  }
}

function pickGroup(g: string) {
  activeGroup.value = g
  load()
}

const allCount = computed(() => groups.value.reduce((sum, g) => sum + g.cnt, 0))

async function unfav(item: FavoriteOut) {
  try {
    await removeFavorite(item.project_id)
    toast.value = '已取消收藏'
    setTimeout(() => (toast.value = ''), 3000)
    load()
  } catch { /* 忽略 */ }
}

function openDetail(id: number) {
  emit('close')
  router.push(`/project/${id}`)
}

// 打开弹窗时加载收藏
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
        <h2>我的收藏</h2>
        <button class="modal-close" title="关闭" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <!-- 分组筛选 -->
      <div class="fav-groups">
        <button class="fav-group-tab" :class="{ active: activeGroup === '' }" @click="pickGroup('')">
          全部 <span class="cnt">{{ allCount }}</span>
        </button>
        <button
          v-for="g in groups"
          :key="g.group_name"
          class="fav-group-tab"
          :class="{ active: activeGroup === g.group_name }"
          @click="pickGroup(g.group_name)"
        >
          {{ g.group_name }} <span class="cnt">{{ g.cnt }}</span>
        </button>
      </div>

      <!-- 搜索 + 排序工具条 -->
      <div class="fav-tools">
        <input
          v-model="searchQ"
          class="fav-search"
          type="text"
          placeholder="搜索收藏：项目名 / 描述"
        />
        <BaseSelect v-model="sortMode" :options="SORT_OPTIONS" class="fav-sort" />
        <span class="fav-count dim">{{ filtered.length }} 个</span>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="error" class="error-banner">{{ error }}</div>
      <div v-else-if="filtered.length === 0" class="empty">暂无收藏项目，去项目列表点击星标收藏即可加入</div>
      <div v-else class="fav-list">
        <div v-for="item in filtered" :key="item.project_id" class="fav-card">
          <div class="fav-main" @click="openDetail(item.project_id)">
            <div class="fav-title">
              <span class="fav-name">{{ item.name }}</span>
              <span class="fav-full">github.com/{{ item.full_name }}</span>
            </div>
            <div class="fav-desc">{{ item.description || '（无简介）' }}</div>
            <div class="fav-meta">
              <span class="tag">{{ item.category }}</span>
              <span v-if="item.language" class="tag lang">{{ item.language }}</span>
              <span class="stars">★ {{ formatStars(item.stars) }}</span>
              <span v-if="item.has_update" class="tag upd">有新版本 {{ item.latest_tag || '' }}</span>
              <span class="dim">收藏于 {{ item.favorited_at.slice(0, 10) }}</span>
            </div>
          </div>
          <div class="fav-ops">
            <button class="btn ghost small" @click="unfav(item)">取消收藏</button>
          </div>
        </div>
      </div>

      <div v-if="toast" class="toast">{{ toast }}</div>
    </div>
  </div>
</template>
