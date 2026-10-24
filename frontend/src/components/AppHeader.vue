<script setup lang="ts">
// 统一顶栏：导航栏仅保留「项目列表」；收藏/采集中心/采集历史/更新历史全部为右侧弹窗入口。
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  fetchHealth,
  fetchNotifications,
  fetchStats,
  markNotificationsRead,
  type HealthResponse,
  type NotificationItem,
} from '../api'
import CollectModal from './CollectModal.vue'
import CategoryCleanupModal from './CategoryCleanupModal.vue'
import FavoritesModal from './FavoritesModal.vue'
import RoundsModal from './RoundsModal.vue'
import SettingsModal from './SettingsModal.vue'
import UpdatesModal from './UpdatesModal.vue'
import { useClickOutside } from '../composables/useClickOutside'

const router = useRouter()
const health = ref<HealthResponse | null>(null)

// 右侧弹窗开关（收藏 / 采集中心 / 采集历史 / 更新历史 / 清除分类）
const favOpen = ref(false)
const collectOpen = ref(false)
const roundsOpen = ref(false)
const updatesOpen = ref(false)
const settingsOpen = ref(false)
const cleanupOpen = ref(false)

// 互斥打开：任一弹窗打开时关闭其余
function openModal(kind: 'fav' | 'collect' | 'rounds' | 'updates' | 'settings' | 'cleanup') {
  favOpen.value = kind === 'fav'
  collectOpen.value = kind === 'collect'
  roundsOpen.value = kind === 'rounds'
  updatesOpen.value = kind === 'updates'
  settingsOpen.value = kind === 'settings'
  cleanupOpen.value = kind === 'cleanup'
  toolsMenuOpen.value = false
}

// 「功能」下拉菜单（收藏 / 采集中心 / 采集历史 / 更新历史入口收纳）
const toolsMenuOpen = ref(false)

// 点外部关闭：容器为「触发按钮 + 弹层」的共同父元素，点按钮本身不触发关闭
const toolsWrapEl = ref<HTMLElement | null>(null)
const bellWrapEl = ref<HTMLElement | null>(null)
useClickOutside(toolsWrapEl, () => { toolsMenuOpen.value = false })
useClickOutside(bellWrapEl, () => { notifOpen.value = false })

// 两个 popover 互斥：开一个自动关另一个，避免叠着显示
function toggleTools() {
  toolsMenuOpen.value = !toolsMenuOpen.value
  if (toolsMenuOpen.value) notifOpen.value = false
}

// ---- 日/夜主题：默认深空星图（暗色），localStorage 记忆 ----
const THEME_KEY = 'starpick-theme'
const theme = ref<'dark' | 'light'>('dark')
function applyTheme(t: 'dark' | 'light') {
  theme.value = t
  document.documentElement.setAttribute('data-theme', t)
  try { localStorage.setItem(THEME_KEY, t) } catch { /* 忽略 */ }
}
function toggleTheme() {
  applyTheme(theme.value === 'dark' ? 'light' : 'dark')
}
function initTheme() {
  let t: 'dark' | 'light' = 'dark'
  try {
    const saved = localStorage.getItem(THEME_KEY)
    if (saved === 'light' || saved === 'dark') t = saved
  } catch { /* 忽略 */ }
  applyTheme(t)
}

const notifications = ref<NotificationItem[]>([])
const unreadCount = ref(0)
const notifOpen = ref(false)
const notifLoading = ref(false)
const notifLimit = ref(15)
const notifLoadedAll = ref(false)

// 变更基线：首次拉取只记值不广播（页面初始加载会拉一遍列表），之后变化才广播。
// total 盯采集增删；ready 盯探测出安装包；pending 盯探测结论为「确无 release」
let lastTotal = -1
let lastAssetsReady = -1
let lastAssetsPending = -1

// 安装包探测进度（还有未探测项目时在状态行展示；无 Token 追加速慢提示）
const probeTotal = ref(0)
const probePending = ref(0)

async function loadMeta() {
  try {
    health.value = await fetchHealth()
  } catch { /* 忽略 */ }
  // 顺带盯项目总数与安装包就绪数：采集落库 / assets 后台探测回填 → 广播给列表页静默刷新。
  // （列表只在筛选条件变化时重载，不通知的话新项目/新探测出的安装包要切一下分类才出现。）
  try {
    const stats = await fetchStats()
    // 三个信号任一变化都广播：总数（采集增删）、ready（探测出安装包）、
    // pending（探测结论为「确无 release」——前两者都不动，只有它减一）
    const changed = lastTotal >= 0
      && (stats.total_projects !== lastTotal
        || stats.assets_ready !== lastAssetsReady
        || stats.assets_pending !== lastAssetsPending)
    if (changed) {
      window.dispatchEvent(new CustomEvent('starpick:projects-changed'))
    }
    lastTotal = stats.total_projects
    lastAssetsReady = stats.assets_ready
    lastAssetsPending = stats.assets_pending
    // 顶栏进度：还有未探测项目时显示「安装包检测 已完成/总数」
    probeTotal.value = stats.total_projects
    probePending.value = stats.assets_pending
  } catch { /* 忽略 */ }
}

async function loadNotifications(reset = false) {
  if (notifLoading.value) return
  if (reset) notifLimit.value = 15
  notifLoading.value = true
  try {
    const data = await fetchNotifications(notifLimit.value)
    notifications.value = data.items
    unreadCount.value = data.unread_count
    notifLoadedAll.value = data.items.length < notifLimit.value
  } catch { /* 忽略 */ } finally {
    notifLoading.value = false
  }
}

// 加载更多：分页拉取更早的通知（通知只按时间倒序展示，替换即可）
async function loadMoreNotifs() {
  if (notifLoading.value || notifLoadedAll.value) return
  notifLimit.value += 15
  await loadNotifications()
}

function toggleNotif() {
  notifOpen.value = !notifOpen.value
  if (notifOpen.value) {
    toolsMenuOpen.value = false   // 与功能菜单互斥
    loadNotifications(true)
  }
}

async function readAll() {
  try {
    await markNotificationsRead([])
    unreadCount.value = 0
    notifications.value.forEach((n) => (n.read_at = new Date().toISOString().slice(0, 19).replace('T', ' ')))
  } catch { /* 忽略 */ }
}

function goNotif(n: NotificationItem) {
  notifOpen.value = false
  if (n.project_id > 0) {
    router.push(`/project/${n.project_id}`)
    if (!n.read_at) {
      markNotificationsRead([n.id]).catch(() => {})
      n.read_at = 'x'
      if (unreadCount.value > 0) unreadCount.value -= 1
    }
  }
}

let timer: ReturnType<typeof setInterval> | undefined
let notifTimer: ReturnType<typeof setInterval> | undefined
/** 卡片「安装包检测中」标签点击 → 打开采集中心（看探测进度） */
function onOpenCollect() {
  openModal('collect')
}

onMounted(() => {
  initTheme()
  loadMeta()
  loadNotifications()
  timer = setInterval(loadMeta, 30000)
  notifTimer = setInterval(loadNotifications, 60000)
  window.addEventListener('starpick:open-collect', onOpenCollect)
})
onUnmounted(() => {
  if (timer) clearInterval(timer)
  if (notifTimer) clearInterval(notifTimer)
  window.removeEventListener('starpick:open-collect', onOpenCollect)
})
</script>

<template>
  <header class="topbar app-header">
    <h1 class="brand">
      <svg class="brand-logo" viewBox="0 0 48 48" aria-hidden="true">
        <path d="M7 31a17 17 0 0 0 34 0" fill="none" stroke="var(--text)" stroke-width="4.5" stroke-linecap="round" />
        <path d="M24 7l2.9 6.6 7.1.6-5.4 4.7 1.6 7-6.2-3.7-6.2 3.7 1.6-7-5.4-4.7 7.1-.6z" fill="var(--accent)" />
      </svg>
      <span class="brand-text">STARPICK</span>
    </h1>
    <!-- 导航：仅一个首页，无额外入口 -->
    <div class="status">
      <span class="dot" :class="health?.status === 'ok' ? 'ok' : health ? 'err' : ''"></span>
      <span>
        后端 {{ health?.status === 'ok' ? '在线' : health ? '异常' : '连接中…' }}
        <template v-if="probePending > 0">
          · 安装包检测 {{ probeTotal - probePending }}/{{ probeTotal }}<template v-if="health && health.has_token === false">（未配 Token，速度受限）</template>
        </template>
      </span>
    </div>
    <!-- 右侧入口：功能下拉菜单（收藏 / 采集中心 / 采集历史 / 更新历史） -->
    <div ref="toolsWrapEl" class="tools-wrap">
      <button class="tools-trigger" :class="{ open: toolsMenuOpen }" title="功能菜单" @click="toggleTools">
        <svg class="tools-grid" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3.5" y="3.5" width="7" height="7" rx="1.8" />
          <rect x="13.5" y="3.5" width="7" height="7" rx="1.8" />
          <rect x="3.5" y="13.5" width="7" height="7" rx="1.8" />
          <rect x="13.5" y="13.5" width="7" height="7" rx="1.8" />
        </svg>
      </button>
      <div v-if="toolsMenuOpen" class="tools-menu">
        <button class="tools-menu-item" @click="openModal('fav')">收藏</button>
        <button class="tools-menu-item" @click="openModal('collect')">采集中心</button>
        <button class="tools-menu-item" @click="openModal('rounds')">采集历史</button>
        <button class="tools-menu-item" @click="openModal('updates')">更新历史</button>
        <button class="tools-menu-item" @click="openModal('cleanup')">清除分类项目</button>
      </div>
    </div>
    <!-- 设置：GitHub Token 热生效配置 -->
    <button class="tools-trigger settings-btn" :class="{ open: settingsOpen }" title="设置" @click="openModal('settings')">
      <svg class="tools-grid" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 7h16M4 12h16M4 17h16" />
        <circle cx="9" cy="7" r="2.2" fill="var(--bg-card)" />
        <circle cx="15" cy="12" r="2.2" fill="var(--bg-card)" />
        <circle cx="8" cy="17" r="2.2" fill="var(--bg-card)" />
      </svg>
    </button>
    <!-- 日/夜切换：图标随当前主题变化 -->
    <button class="theme-btn" :class="{ open: theme === 'light' }" :title="theme === 'dark' ? '切换到日间模式' : '切换到深空星图'" @click="toggleTheme">
      <svg v-if="theme === 'dark'" class="theme-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
      <svg v-else class="theme-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
      </svg>
    </button>
    <!-- 通知铃铛：与导航栏保持间距 -->
    <div ref="bellWrapEl" class="bell-wrap">
      <button class="bell-btn" :class="{ open: notifOpen }" title="通知中心" @click="toggleNotif">
        <svg class="bell-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        <span v-if="unreadCount > 0" class="bell-badge">{{ unreadCount > 99 ? '99+' : unreadCount }}</span>
      </button>
      <div v-if="notifOpen" class="notif-panel">
        <div class="notif-head">
          <span>通知中心</span>
          <button class="btn ghost small" :disabled="unreadCount === 0" @click="readAll">全部已读</button>
        </div>
        <div v-if="notifications.length === 0" class="empty">暂无通知</div>
        <div v-else class="notif-list">
          <div
            v-for="n in notifications"
            :key="n.id"
            class="notif-item"
            :class="{ unread: !n.read_at }"
            @click="goNotif(n)"
          >
            <span v-if="!n.read_at" class="notif-dot"></span>
            <div class="notif-body">
              <div class="notif-title">{{ n.title }}</div>
              <div class="notif-msg">{{ n.message }}</div>
              <div class="notif-time">{{ n.created_at.slice(5, 16).replace('T', ' ') }}</div>
            </div>
          </div>
        </div>
        <!-- 加载更多 -->
        <div v-if="notifications.length > 0 && !notifLoadedAll" class="notif-more">
          <button class="btn ghost small" :disabled="notifLoading" @click="loadMoreNotifs">
            {{ notifLoading ? '加载中…' : '加载更多' }}
          </button>
        </div>
        <div v-else-if="notifications.length > 0 && notifLoadedAll" class="notif-more dim">
          已显示全部通知
        </div>
      </div>
    </div>
  </header>

  <FavoritesModal :open="favOpen" @close="favOpen = false" />
  <CollectModal :open="collectOpen" @close="collectOpen = false" @open-settings="openModal('settings')" />
  <CategoryCleanupModal :open="cleanupOpen" @close="cleanupOpen = false" />
  <RoundsModal :open="roundsOpen" @close="roundsOpen = false" />
  <UpdatesModal :open="updatesOpen" @close="updatesOpen = false" />
  <SettingsModal :open="settingsOpen" @close="settingsOpen = false" />
</template>
