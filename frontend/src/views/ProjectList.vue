<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ProjectCard from '../components/ProjectCard.vue'
import {
  fetchDailyPick,
  fetchShufflePick,
  fetchCategories,
  fetchProjects,
  type DailyPick,
  type Project,
} from '../api'
import AppHeader from '../components/AppHeader.vue'

const route = useRoute()
const router = useRouter()

// ---- 状态：以 URL query 为唯一数据源（刷新/前进后退/分享链接均保留筛选） ----
const projects = ref<Project[]>([])
const total = ref(0)
const pageSize = 20

const q = route.query
const category = ref(typeof q.category === 'string' ? q.category : '')
const keyword = ref(typeof q.keyword === 'string' ? q.keyword : '')
const lang = ref(typeof q.lang === 'string' ? q.lang : '')
const onlyNew = ref(typeof q.new === 'string' ? q.new : '')
const onlyZh = ref(typeof q.zh === 'string' ? q.zh : '')
const platform = ref(typeof q.platform === 'string' ? q.platform : '')
const sort = ref(typeof q.sort === 'string' ? q.sort : 'stars')
const page = ref(parseInt(typeof q.page === 'string' ? q.page : '1', 10) || 1)
const loading = ref(false)
const loadingMore = ref(false)
const error = ref('')

// 分类列表：内置分类 + 自定义分类（自定义分类采集后自动出现在首页筛选）
// chip 上附带各分类项目数（'全部' 显示总数，加载后填充）
const CATEGORIES = ref<string[]>(['', '影视', '视频工具', '游戏', '游戏开发', '开发', 'AI工具', '效率工具', '加速工具', '设计', '创作', '音乐', '阅读'])
const categoryCounts = ref<Record<string, number>>({})
fetchCategories()
  .then((r) => {
    const list = r.categories ?? []
    CATEGORIES.value = ['', ...list]
    categoryCounts.value = r.counts ?? {}
  })
  .catch(() => { /* 后端暂不可用：保留内置兜底 */ })
const LANGS = [
  { value: '', label: '全部语言' },
  { value: 'zh', label: '中文' },
  { value: 'en', label: '英文' },
]
// 平台筛选：值与入库的 platforms JSON 数组取值一致（requirements 推断），与分类平级的独立维度
const PLATFORMS = ['Windows', 'macOS', 'Linux', 'Android', 'iOS', 'Web', 'Docker']
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

// 最近一次列表查询条件（详情页返回时恢复）
const LIST_QUERY_KEY = 'listQuery'

/** 把当前筛选状态写入 URL query（省略默认值，保持链接干净） */
function applyQuery() {
  const query: Record<string, string> = {}
  if (category.value) query.category = category.value
  if (keyword.value.trim()) query.keyword = keyword.value.trim()
  if (lang.value) query.lang = lang.value
  if (onlyNew.value) query.new = onlyNew.value
  if (onlyZh.value) query.zh = onlyZh.value
  if (platform.value) query.platform = platform.value
  if (sort.value !== 'stars') query.sort = sort.value
  if (page.value > 1) query.page = String(page.value)
  router.replace({ query })
}

/** 是否有任一筛选生效（空态文案与「清除筛选」按钮用） */
function hasActiveFilters() {
  return Boolean(category.value || keyword.value.trim() || lang.value || onlyNew.value || onlyZh.value || platform.value)
}

/** 一键清除全部筛选回全部列表 */
function clearFilters() {
  router.replace({ query: {} })
}

// ---- 数据加载：append=true 追加下一页（触底），false 替换（筛选/搜索/翻页/URL 变化） ----
async function loadProjects(append = false) {
  if (append) loadingMore.value = true
  else loading.value = true
  error.value = ''
  try {
    const data = await fetchProjects({
      category: category.value || undefined,
      keyword: keyword.value.trim() || undefined,
      lang: lang.value || undefined,
      new: onlyNew.value || undefined,
      zh: onlyZh.value || undefined,
      platform: platform.value || undefined,
      page: page.value,
      page_size: pageSize,
      sort: sort.value,
    })
    if (append) {
      const seen = new Set(projects.value.map((p) => p.id))
      const fresh = data.items.filter((p) => !seen.has(p.id))
      projects.value = [...projects.value, ...fresh]
    } else {
      projects.value = data.items
    }
    total.value = data.total
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载项目失败'
  } finally {
    if (append) loadingMore.value = false
    else loading.value = false
  }
}

// ---- URL query 变化（点击筛选 / 搜索 / 翻页 / 刷新 / 前进后退 / 直接改地址）→ 同步状态并加载 ----
function saveSnapshot(pageOverride?: number) {
  try {
    const snapshot: Record<string, string> = {}
    for (const key of ['category', 'keyword', 'lang', 'new', 'zh', 'platform', 'sort']) {
      const v = route.query[key]
      if (typeof v === 'string' && v) snapshot[key] = v
    }
    const p = pageOverride ?? (typeof route.query.page === 'string' ? route.query.page : '')
    if (p && Number(p) > 1) snapshot.page = String(p)
    sessionStorage.setItem(LIST_QUERY_KEY, JSON.stringify(snapshot))
  } catch { /* 隐私模式忽略 */ }
}

watch(
  () => route.query,
  () => {
    const q2 = route.query
        category.value = typeof q2.category === 'string' ? q2.category : ''
        keyword.value = typeof q2.keyword === 'string' ? q2.keyword : ''
        lang.value = typeof q2.lang === 'string' ? q2.lang : ''
        onlyNew.value = typeof q2.new === 'string' ? q2.new : ''
        onlyZh.value = typeof q2.zh === 'string' ? q2.zh : ''
        platform.value = typeof q2.platform === 'string' ? q2.platform : ''
        sort.value = typeof q2.sort === 'string' ? q2.sort : 'stars'
    page.value = parseInt(typeof q2.page === 'string' ? q2.page : '1', 10) || 1
    // 同步会话记忆：任何 URL 变化都记录，详情页返回可恢复（含直接改地址/刷新场景）
    saveSnapshot()
    loadProjects(false)
  },
  { immediate: true },
)

// ---- 滚动触底自动加载下一页 ----
const sentinel = ref<HTMLElement | null>(null)
let io: IntersectionObserver | null = null

function loadMore() {
  if (loadingMore.value || loading.value) return
  if (page.value >= totalPages.value) return
  const next = page.value + 1
  // 关键：不用 router.replace（会触发 scrollBehavior 强制回顶 + 整表重渲染），
  // 直接 history.replaceState 改 URL 页码，再手动追加数据，滚动位置保持不动。
  const url = new URL(window.location.href)
  url.searchParams.set('page', String(next))
  window.history.replaceState({}, '', url)
  page.value = next
  saveSnapshot(next)
  loadProjects(true)
}

// ---- 采集落库后静默刷新：AppHeader 轮询发现项目总数变化时广播此事件 ----
// 无 loading 闪烁：仅当前在第一页且无筛选/搜索时重拉第一页（新项目按现排序插进列表），
// 处于翻页深处/带筛选时不打扰（重拉会跳页、且筛选集合可能没命中新项目）。
async function silentReload() {
  if (loading.value || loadingMore.value) return
  if (page.value !== 1 || category.value || keyword.value.trim() || onlyNew.value || onlyZh.value || platform.value) return
  try {
    const data = await fetchProjects({
      lang: lang.value || undefined,
      zh: onlyZh.value || undefined,
      platform: platform.value || undefined,
      page: 1,
      page_size: pageSize,
      sort: sort.value,
    })
    // 签名含各项目 assets 数与探测标记：assets 后台探测回填不改总数、不动排序，
    // 但卡片要在「检测中 → 下载版本/无安装包」之间翻转；纯浏览量等无关变化仍不打扰 DOM
    const sig = (items: { id: number; assets: unknown[]; assets_probed?: boolean }[]) =>
      items.map((p) => `${p.id}:${p.assets.length}:${p.assets_probed ? 1 : 0}`).join(',')
    if (data.total === total.value && sig(data.items) === sig(projects.value)) return
    projects.value = data.items
    total.value = data.total
  } catch { /* 下次轮询再试 */ }
}

onMounted(() => {
  if (!('IntersectionObserver' in window)) return
  io = new IntersectionObserver(
    (entries) => {
      if (entries[0]?.isIntersecting) loadMore()
    },
    { rootMargin: '400px' },
  )
  // 哨兵出现（列表渲染 / 追加后重建）时绑定观察
  watch(
    sentinel,
    (el) => {
      if (el) {
        io?.disconnect()
        io?.observe(el)
      }
    },
    { flush: 'post' },
  )
  window.addEventListener('starpick:projects-changed', silentReload)
})
onUnmounted(() => {
  io?.disconnect()
  window.removeEventListener('starpick:projects-changed', silentReload)
})

// ---- 交互：只改状态并写 URL（加载由上面的 watch 统一触发，避免双重请求） ----
function selectCategory(c: string) {
  category.value = c
  page.value = 1
  applyQuery()
}

// 「中文」走中文友好口径（zh=1，比简介语言判定更全）；「英文」仍按简介语言。组内单选。
/** 语言组激活态：''（全部）只在整组无选择时亮——选中文时 lang 为空但 zh=1，不能两个同亮 */
function isLangActive(v: string) {
  if (v === '') return !lang.value && onlyZh.value !== '1'
  if (v === 'zh') return onlyZh.value === '1'
  return lang.value === v
}

function selectLang(l: string) {
  if (l === 'zh') {
    const on = onlyZh.value === '1'
    onlyZh.value = on ? '' : '1'
    lang.value = ''
  } else {
    lang.value = l
    onlyZh.value = ''   // 单选：切到其它语言时取消中文
  }
  page.value = 1
  applyQuery()
}

// 「新增」独立条件：与分类/语言互不干扰，点击开关
function selectNew() {
  onlyNew.value = onlyNew.value ? '' : 'new'
  page.value = 1
  applyQuery()
}

function selectPlatform(p: string) {
  platform.value = platform.value === p ? '' : p   // 点同一个平台 = 取消
  page.value = 1
  applyQuery()
}

// ---- 排序下拉：自绘组件（原生 select 弹出 UI 不可控） ----
const SORT_OPTIONS = [
  { value: 'stars', label: 'Star 最多' },
  { value: 'recent', label: '最近活跃' },
  { value: 'new', label: '最近入库' },
]
const sortOpen = ref(false)
const sortLabel = computed(() => SORT_OPTIONS.find((o) => o.value === sort.value)?.label ?? 'Star 最多')

function toggleSort() {
  sortOpen.value = !sortOpen.value
}

function pickSort(v: string) {
  sortOpen.value = false
  if (sort.value !== v) {
    sort.value = v
    page.value = 1
    applyQuery()
  }
}

// 点击其它区域关闭排序菜单
function onDocClick(e: MouseEvent) {
  if (sortOpen.value && !(e.target as HTMLElement).closest('.sort-select')) {
    sortOpen.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))

// ---- 实时搜索：输入即搜（防抖 300ms），无需点击搜索按钮 ----
let debounceTimer: ReturnType<typeof setTimeout> | undefined
watch(keyword, () => {
  if (debounceTimer) clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    page.value = 1
    applyQuery()
  }, 300)
})

// 回车立即搜索（跳过防抖），同时清掉挂起的防抖定时器避免重复请求
function searchNow() {
  if (debounceTimer) clearTimeout(debounceTimer)
  page.value = 1
  applyQuery()
}

// 清空输入框：同步清 URL 并聚焦回输入框
const searchInput = ref<HTMLInputElement | null>(null)
function clearKeyword() {
  if (debounceTimer) clearTimeout(debounceTimer)
  keyword.value = ''
  page.value = 1
  applyQuery()
  searchInput.value?.focus()
}

function hasKeyword(): boolean {
  return keyword.value.trim().length > 0
}

// 与 GitHub 一致：使用 k 缩写（不使用万）
function formatK(n: number): string {
  if (n >= 1000) {
    const v = n / 1000
    return (Number.isInteger(v) ? v.toFixed(0) : v.toFixed(1).replace(/\.0$/, '')) + 'k'
  }
  return String(n)
}

// ---- 每日精选 ----
const dailyPick = ref<DailyPick | null>(null)
const shuffleLoading = ref(false)

async function loadDailyPick() {
  try {
    dailyPick.value = await fetchDailyPick()
  } catch { /* 无精选则隐藏 */ }
}

// 「换一批」：随机抽取 5 个替换当前展示（不落库）
async function shufflePick() {
  if (shuffleLoading.value) return
  shuffleLoading.value = true
  try {
    dailyPick.value = await fetchShufflePick()
  } catch { /* 保持原内容 */ } finally {
    shuffleLoading.value = false
  }
}

onMounted(() => {
  loadDailyPick()
})
onUnmounted(() => {
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <div class="container">
    <AppHeader />

    <!-- 每日精选 -->
    <div v-if="dailyPick && dailyPick.items.length" class="picks-strip">
      <div class="picks-head">
        <div class="picks-title">
          <span>每日精选</span>
          <span v-if="dailyPick.pick_date" class="picks-date">{{ dailyPick.pick_date }}</span>
        </div>
        <button class="picks-shuffle" :disabled="shuffleLoading" @click="shufflePick">
          <span class="picks-shuffle-icon" :class="{ spinning: shuffleLoading }">↻</span>
          {{ shuffleLoading ? '换一批…' : '换一批' }}
        </button>
      </div>
      <div class="picks-list">
        <router-link
          v-for="p in dailyPick.items"
          :key="p.id"
          :to="`/project/${p.id}`"
          class="pick-card"
        >
          <span class="pick-rank">#{{ p.rank }}</span>
          <span class="pick-name">{{ p.name }}</span>
          <span class="tag">{{ p.category }}</span>
          <span class="stars">★ {{ formatK(p.stars) }}</span>
        </router-link>
      </div>
    </div>

    <!-- 统计 -->
    <!-- 筛选：分类 / 语言 / 新增 分组显示，搜索独立一行 -->
    <div class="filters">
      <div class="filter-group">
        <span class="filter-label">分类</span>
        <button
          v-for="c in CATEGORIES"
          :key="c"
          class="chip"
          :class="{ active: category === c }"
          @click="selectCategory(c)"
        >
          {{ c || '全部' }}<span class="chip-count">{{ c ? (categoryCounts[c] ?? '') : total }}</span>
        </button>
      </div>
      <div class="filter-group">
        <span class="filter-label">语言</span>
        <button
          v-for="l in LANGS"
          :key="l.value"
          class="chip lang-chip"
          :class="{ active: isLangActive(l.value) }"
          :title="l.value === 'zh' ? '中文友好项目：简介为中文，或对中文用户友好（含中文文档/界面）' : undefined"
          @click="selectLang(l.value)"
        >
          {{ l.label }}
        </button>
      </div>
      <div class="filter-group">
        <span class="filter-label">新增</span>
        <button class="chip new-chip" :class="{ active: onlyNew === 'new' }" @click="selectNew">
          新增
        </button>
      </div>
      <div class="filter-group">
        <span class="filter-label">平台</span>
        <button
          v-for="p in PLATFORMS"
          :key="p"
          class="chip platform-chip"
          :class="{ active: platform === p }"
          @click="selectPlatform(p)"
        >
          {{ p }}
        </button>
      </div>
    </div>
    <div class="search-row">
      <span class="filter-label">搜索</span>
      <div class="search-box">
        <input ref="searchInput" v-model="keyword" class="search-input" placeholder="输入即搜：项目名 / 描述" @keyup.enter="searchNow" />
      </div>
      <button v-if="hasKeyword()" class="search-clear" title="清空" @click="clearKeyword">×</button>
    </div>

    <!-- 排序 -->
    <div class="sort-row">
      <span>排序：</span>
      <div class="sort-select" :class="{ open: sortOpen }" @click="toggleSort">
        <span class="sort-value">{{ sortLabel }}</span>
        <span class="sort-caret" aria-hidden="true"></span>
        <div v-if="sortOpen" class="sort-menu">
          <button
            v-for="o in SORT_OPTIONS"
            :key="o.value"
            class="sort-option"
            :class="{ active: sort === o.value }"
            @click="pickSort(o.value)"
          >
            {{ o.label }}
          </button>
        </div>
      </div>
      <span>共 {{ total }} 个项目</span>
    </div>

    <div v-if="error" class="error-banner">{{ error }}</div>

    <!-- 列表 -->
    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="projects.length === 0" class="empty">
      <template v-if="hasActiveFilters()">
        没有匹配的项目——换个关键词或筛选条件试试，
        <button class="link-btn" @click="clearFilters">查看全部</button>
      </template>
      <template v-else-if="onlyNew === 'new'">近 72 小时暂无新增项目。可前往「采集中心」触发一轮搜索。</template>
      <template v-else>暂无项目。可前往「采集中心」手动触发一轮搜索。</template>
    </div>
    <div v-else class="grid">
      <ProjectCard v-for="p in projects" :key="p.id" :project="p" />
    </div>

    <!-- 触底自动加载：滚动到列表底部自动追加下一页 -->
    <div v-if="total > projects.length" ref="sentinel" class="load-more">
      <span class="hint">{{ loadingMore ? '加载中…' : `已加载 ${projects.length} / 共 ${total} 个项目` }}</span>
    </div>
    <div v-else-if="projects.length > 0" class="load-more">
      <span class="hint">已全部加载（共 {{ total }} 个项目）</span>
    </div>
  </div>
</template>
