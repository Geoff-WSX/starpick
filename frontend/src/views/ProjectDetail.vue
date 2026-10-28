<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import {
  addFavorite,
  detectOS,
  fetchHealthSnapshots,
  fetchProjectDetail,
  fetchReadmeVariant,
  fetchSimilarProjects,
  formatDate,
  formatSize,
  formatStars,
  openExternal,
  removeFavorite,
  type HealthSnapshot,
  type Project,
  type ProjectDetail,
} from '../api'
import InstallPromptModal from '../components/InstallPromptModal.vue'

const props = defineProps<{ id: string }>()
const router = useRouter()

const detail = ref<ProjectDetail | null>(null)
const loading = ref(true)
const error = ref('')
const currentOS = detectOS()

// 当前系统匹配的安装包（下载区默认项）
const matchedAssets = computed(() => {
  const d = detail.value
  if (!d || !currentOS) return []
  return d.assets.filter((a) => a.platform === currentOS)
})

/** GitHub 直链 → 镜像加速链接（gh-proxy 系列：前缀 + 完整原始 URL 即转发） */
function mirrorUrl(url: string): string {
  const p = detail.value?.mirror_prefixes?.[0]
  if (!p || !url.startsWith('https://github.com/')) return ''
  return `${p}${url}`
}

function external(url: string) {
  if (url) openExternal(url)
}

/** 优先走镜像加速；无镜像前缀时退回原始链接 */
function downloadBest(url: string) {
  external(mirrorUrl(url) || url)
}

// ---- 安装提示词：先弹窗看全文，再一键复制给豆包等能操作电脑的 AI 助手安装 ----
const showInstallPrompt = ref(false)

function openInstallPrompt() {
  if (detail.value) showInstallPrompt.value = true
}

const projectId = computed(() => Number(props.id))

// 附加分类（跨界项目多分类标注）：categories 中除主分类外的标签
const extraCategories = computed(() => {
  const d = detail.value
  if (!d) return []
  return (d.categories ?? []).filter((c) => c !== d.category)
})

// 相似项目推荐（同分类 + star 数量级相近）
const similar = ref<Project[]>([])
const similarLoading = ref(false)
async function loadSimilar() {
  similarLoading.value = true
  try {
    const data = await fetchSimilarProjects(projectId.value)
    similar.value = data.items
  } catch { /* 无相似推荐则隐藏 */ } finally {
    similarLoading.value = false
  }
}

async function load() {
  loading.value = true
  error.value = ''
  // 切换项目时清空按需加载的语言变体，避免残留上一个项目的内容
  extraVariant.value = null
  extraError.value = ''
  extraErrorPath.value = ''
  extraLoading.value = ''
  try {
    detail.value = await fetchProjectDetail(projectId.value)
    isFav.value = !!detail.value.is_favorite
    favGroup.value = detail.value.favorite_group ?? ''
    loadHealth()
    loadSimilar()
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载项目失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
watch(() => props.id, load)

// ---- README 语言切换（原文 / 中文 / English / 按需加载的其它语言） ----
type ReadmeLangKey = 'default' | 'zh' | 'en' | 'extra'
const readmeLang = ref<ReadmeLangKey>('default')

// 原文正文是否以中文为主（CJK 占比 > 0.5%，与后端 detect_zh_friendly 阈值一致）
function isChineseReadme(text: string): boolean {
  if (!text) return false
  const sample = text.slice(0, 20000)
  const cjk = sample.match(/[\u4e00-\u9fff]/g)
  return !!cjk && cjk.length / sample.length > 0.005
}

// ---- 语言变体文件名识别（与后端 app/github_api.py 的 variant_lang 同一份表） ----
const VARIANT_NAME_RE = /^readme[._-]([a-z0-9._-]+)\.(?:md|markdown|mdx)$/i
const ZH_CODES = new Set(['zh', 'cn', 'chs', 'hans', 'cmn', 'zhcn', 'cnzh', 'chinese'])
const ZH_HANT_CODES = new Set(['zht', 'tw', 'hk', 'hant', 'tcn', 'zhtw', 'zhhk', 'traditional', 'fan'])
const EN_CODES = new Set(['en', 'eng', 'english'])
const OTHER_LANG_CODES = new Set([
  'ar', 'az', 'be', 'bg', 'bn', 'ca', 'cs', 'da', 'de', 'el', 'es', 'et', 'eu',
  'fa', 'fi', 'fr', 'gl', 'he', 'hi', 'hd', 'hr', 'hu', 'hy', 'id', 'is', 'it', 'ja',
  'jp', 'ka', 'kk', 'km', 'kn', 'ko', 'kr', 'ky', 'lo', 'lt', 'lv', 'mk', 'ml',
  'mn', 'mr', 'ms', 'my', 'ne', 'nl', 'no', 'pa', 'pl', 'ps', 'pt', 'ro', 'ru',
  'si', 'sk', 'sl', 'sq', 'sr', 'sv', 'sw', 'ta', 'te', 'th', 'tl', 'tr', 'ua',
  'uk', 'ur', 'uz', 'vi',
  // 区域双码（README.pt-BR.md / ja-JP / ko-KR 等，压缩后首码对应主语言）
  'ptbr', 'jajp', 'kokr', 'enus', 'ruru', 'eses', 'trtr', 'nlnl', 'frfr', 'idid', 'dede',
])
const DEFAULT_README_NAMES = ['README.md', 'readme.md', 'README', 'Readme.md']

/** 变体文件名 → 语言键（zh / zh-Hant / en / 其它语言代码）；非语言变体返回 null */
function variantLang(basename: string): string | null {
  const m = VARIANT_NAME_RE.exec(basename)
  if (!m) return null
  const code = m[1].replace(/^[._-]+|[._-]+$/g, '').toLowerCase().replace(/_/g, '-')
  const compact = code.replace(/-/g, '')
  if (ZH_CODES.has(compact) || /^(zh-cn|zh-hans|cmn-hans)/.test(code)) return 'zh'
  if (ZH_HANT_CODES.has(compact) || /^(zh-tw|zh-hk|zh-hant|cmn-hant)/.test(code)) return 'zh-Hant'
  if (EN_CODES.has(compact) || code.startsWith('en-')) return 'en'
  if (OTHER_LANG_CODES.has(compact)) return compact
  const head = code.split('-')[0]
  return OTHER_LANG_CODES.has(head) ? head : null
}

/** 相对路径归一化（去空段/./，解析 ..）；越出仓库根返回 null */
function normalizeRepoPath(raw: string): string | null {
  const parts: string[] = []
  for (const seg of raw.split('/')) {
    if (!seg || seg === '.') continue
    if (seg === '..') {
      if (!parts.length) return null
      parts.pop()
    } else {
      parts.push(seg)
    }
  }
  return parts.length ? parts.join('/') : null
}

/** README 链接 → 本仓库内相对路径（外链/锚点/其它仓库返回 null；baseDir 为按需变体所在目录） */
function repoRelativePath(href: string, fullName: string, baseDir: string): string | null {
  const clean = href.trim().split('#')[0].split('?')[0]
  if (!clean) return null
  if (/^[a-z][a-z0-9+.-]*:/i.test(clean)) {
    // GitHub blob / raw 绝对链接也能还原（项目常常把语言切换写成绝对地址）
    const m = /^https?:\/\/(?:github\.com\/([^/\s]+\/[^/\s]+)\/(?:blob|raw)|raw\.githubusercontent\.com\/([^/\s]+\/[^/\s]+))\/[^/\s]+\/(.+)$/i.exec(clean)
    if (!m) return null
    const repo = m[1] || m[2]
    if (repo.toLowerCase() !== fullName.toLowerCase()) return null
    return normalizeRepoPath(m[3])
  }
  return normalizeRepoPath(baseDir ? `${baseDir}/${clean}` : clean)
}

function escapeHtmlAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

// 语言选项：仅展示项目自带语言版本。无独立中文变体时不再提供假「中文」选项；
// 若原文本身即中文，则把「原文」标签直接标注为「中文」，避免误导。
const readmeLangs = computed(() => {
  const d = detail.value
  if (!d) return []
  const opts: { key: ReadmeLangKey; label: string }[] = []
  const originalIsChinese = !d.readme_zh && isChineseReadme(d.readme)
  if (d.readme) opts.push({ key: 'default', label: originalIsChinese ? '中文' : '原文' })
  if (d.readme_zh) opts.push({ key: 'zh', label: '中文' })
  if (d.readme_en) opts.push({ key: 'en', label: 'English' })
  if (extraVariant.value) {
    // 点击时才抓的版本标注「按需」，库里已采的直接用语言名
    const raw = extraVariant.value.label
    const label = extraVariant.value.source === 'fetch' && opts.some((o) => o.label === raw)
      ? `${raw}（按需）`
      : raw
    opts.push({ key: 'extra', label })
  }
  return opts
})

watch(readmeLangs, (opts) => {
  // 默认展示中文版（项目定位中文用户）；无中文版则回到原文
  if (!opts.some((o) => o.key === readmeLang.value)) {
    readmeLang.value = opts.some((o) => o.key === 'zh') ? 'zh' : 'default'
  }
})

// ---- README 渲染（相对路径 → 镜像 raw 直链；DOMPurify 消毒防 XSS） ----
// raw.githubusercontent.com 在当前网络常被重置，图片统一走后端下发的镜像前缀
const RAW_HOST = 'https://raw.githubusercontent.com'

function toMirrorUrl(href: string, mirrorPrefix: string): string {
  return `${mirrorPrefix}${href}`
}

function resolveReadme(
  md: string,
  fullName: string,
  htmlUrl: string,
  mirrorPrefix: string,
  baseDir = '',
): string {
  const base = htmlUrl.replace(/\/$/, '')
  // 图片：相对路径 → 镜像 raw 直链；raw 绝对路径 → 加镜像前缀
  md = md.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (m, alt, href: string) => {
    if (/^[a-z][a-z0-9+.-]*:/i.test(href)) {
      if (href.startsWith(RAW_HOST) && mirrorPrefix) {
        return `![${alt}](${toMirrorUrl(href, mirrorPrefix)})`
      }
      return m
    }
    const p = repoRelativePath(href, fullName, baseDir)
    if (!p) return m
    return `![${alt}](${toMirrorUrl(`${RAW_HOST}/${fullName}/HEAD/${p}`, mirrorPrefix)})`
  })
  // HTML <img> 标签：部分 README 用 HTML 而非 Markdown 语法写图片（如 <img src="./public/logo.png">）
  md = md.replace(/<img\s+[^>]*src="([^"]+)"[^>]*>/gi, (m, src: string) => {
    let newSrc = src
    if (/^[a-z][a-z0-9+.-]*:/i.test(src)) {
      // data:、https: 等绝对协议：仅 raw 需要镜像
      if (src.startsWith(RAW_HOST) && mirrorPrefix) newSrc = toMirrorUrl(src, mirrorPrefix)
    } else if (!src.startsWith('#') && !src.startsWith('//')) {
      const p = repoRelativePath(src, fullName, baseDir)
      if (p) newSrc = toMirrorUrl(`${RAW_HOST}/${fullName}/HEAD/${p}`, mirrorPrefix)
    }
    return m.replace(`src="${src}"`, `src="${newSrc}"`)
  })
  // HTML <a> 链接：部分 README 用 HTML 而非 Markdown 写语言切换（如 transformers 的
  // <a href="i18n/README_zh-hans.md">简体中文</a>）——同样转站内切换，否则点击跳相对路由变空白页
  md = md.replace(/<a\s+[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, (m, href: string, inner: string) => {
    if (href.startsWith('#')) return m
    if (/^[a-z][a-z0-9+.-]*:/i.test(href)) return m   // 绝对协议（http/mailto 等）保持原样
    const p = repoRelativePath(href, fullName, baseDir)
    if (!p) return m
    const file = p.split('/').pop() || ''
    const lang = variantLang(file)
    if (lang) {
      const label = inner.replace(/<[^>]+>/g, '').trim()
      return `<a href="#readme-local" data-variant="${escapeHtmlAttr(p)}" data-lang="${escapeHtmlAttr(lang)}" data-label="${escapeHtmlAttr(label)}" class="lang-switch" title="在本站内查看该语言版本">${inner}</a>`
    }
    if (!p.includes('/') && DEFAULT_README_NAMES.some((n) => n.toLowerCase() === file.toLowerCase())) {
      return `<a href="#readme-default" data-lang="default" class="lang-switch" title="在本站内切换语言">${inner}</a>`
    }
    return m   // 其它相对路径的 HTML 链接保持原样（GitHub 页内跳转）
  })
  // 链接：语言变体（含子目录/绝对链接写法）→ 站内语言切换；其余相对路径 → GitHub blob 页
  // 文本允许嵌套图片（徽章式 [![xx](badge)](path)）——旧正则 [^\]]* 会把嵌套的 ]( 截断，
  // 语言链接被误当普通链接 → 点击跳相对路由变空白页（2026-10-28 定位）
  md = md.replace(/\[((?:[^\[\]]|\[[^\]]*\])*)\]\(([^)]+)\)/g, (m, text: string, href: string) => {
    if (href.startsWith('#')) return m
    const p = repoRelativePath(href, fullName, baseDir)
    if (!p) return m
    const file = p.split('/').pop() || ''
    const lang = variantLang(file)
    if (lang) {
      return `<a href="#readme-local" data-variant="${escapeHtmlAttr(p)}" data-lang="${escapeHtmlAttr(lang)}" data-label="${escapeHtmlAttr(text)}" class="lang-switch" title="在本站内查看该语言版本">${text}</a>`
    }
    if (!p.includes('/') && DEFAULT_README_NAMES.some((n) => n.toLowerCase() === file.toLowerCase())) {
      // 只有仓库根目录的默认 README 才是「切回原文」；子目录 README.md（模块文档）走下方 GitHub 链接
      return `<a href="#readme-default" data-lang="default" class="lang-switch" title="在本站内切换语言">${text}</a>`
    }
    return `[${text}](${base}/blob/HEAD/${p})`
  })
  return md
}

// ---- 语言变体展示：库内已采集的即时渲染，缺失的点击时经后端镜像按需抓取 ----
type VariantView = { path: string; label: string; content: string; baseDir: string; source: 'local' | 'fetch' }
const extraVariant = ref<VariantView | null>(null)
const extraLoading = ref('')
const extraError = ref('')
const extraErrorPath = ref('')

const extraFallbackUrl = computed(() => {
  const p = extraVariant.value?.path || extraErrorPath.value
  return detail.value && p ? `${detail.value.html_url.replace(/\/$/, '')}/blob/HEAD/${p}` : ''
})

/** 库内已采集的变体正文：中/英在各自列，其余语言在 readme_variants（采集时按 README 声明全量入库） */
function localVariant(lang: string): string {
  const d = detail.value
  if (!d) return ''
  if (lang === 'zh') return d.readme_zh || ''
  if (lang === 'en') return d.readme_en || ''
  return d.readme_variants?.[lang] || ''
}

function baseDirOf(path: string): string {
  return path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
}

/** 用库内正文直接渲染成「额外语言」tab（无网络请求） */
function showLocalVariant(lang: string, path: string, label: string) {
  extraError.value = ''
  extraErrorPath.value = ''
  extraLoading.value = ''
  extraVariant.value = {
    path,
    label: label || path.split('/').pop() || lang,
    content: localVariant(lang),
    baseDir: baseDirOf(path),
    source: 'local',
  }
  readmeLang.value = 'extra'
}

async function loadExtraVariant(path: string, label: string) {
  extraError.value = ''
  extraErrorPath.value = ''
  extraLoading.value = label
  try {
    const data = await fetchReadmeVariant(projectId.value, path)
    extraVariant.value = {
      path: data.path,
      label,
      content: data.content,
      baseDir: baseDirOf(data.path),
      source: 'fetch',
    }
    readmeLang.value = 'extra'
  } catch {
    extraErrorPath.value = path
    extraError.value = `「${label}」版本暂时无法通过镜像获取（可能已改名或删除）`
  } finally {
    extraLoading.value = ''
  }
}

function switchLang(key: ReadmeLangKey) {
  extraError.value = ''
  readmeLang.value = key
}

/** README 正文内项目自带的「English | 中文 | 日本語…」切换链接 → 站内切换（事件委托） */
async function onReadmeClick(e: MouseEvent) {
  const target = (e.target as HTMLElement).closest('a[data-lang]') as HTMLAnchorElement | null
  if (!target) return
  const lang = target.dataset.lang || 'default'
  const path = target.dataset.variant || ''
  const label = target.dataset.label || ''
  if (lang === 'default') {
    e.preventDefault()
    switchLang('default')
    return
  }
  if (!path) return // 无路径信息：保持默认跳转行为
  e.preventDefault()
  if (lang === 'zh' || lang === 'en') {
    // 中/英有专属 tab；库里没有（旧数据）时也走按需抓取兜底
    if (localVariant(lang)) {
      switchLang(lang)
      return
    }
    await loadExtraVariant(path, label || path.split('/').pop() || lang)
    return
  }
  const local = localVariant(lang)
  if (local) {
    showLocalVariant(lang, path, label) // 采集时已入库 → 即时切换，不请求网络
    return
  }
  await loadExtraVariant(path, label || path.split('/').pop() || '其它语言')
}

const mirrorPrefix = computed(() => detail.value?.mirror_prefixes?.[0] ?? '')

const readmeHtml = computed(() => {
  const d = detail.value
  if (!d) return ''
  let md = ''
  let baseDir = ''
  if (readmeLang.value === 'extra') {
    if (!extraVariant.value) return ''
    md = extraVariant.value.content
    baseDir = extraVariant.value.baseDir
  } else {
    md = readmeLang.value === 'zh' ? d.readme_zh
      : readmeLang.value === 'en' ? d.readme_en
      : d.readme
  }
  if (!md) return ''
  const resolved = resolveReadme(md, d.full_name, d.html_url, mirrorPrefix.value, baseDir)
  return DOMPurify.sanitize(marked.parse(resolved, { async: false }) as string, {
    ADD_ATTR: ['target', 'rel'],
  })
})

// ---- 收藏（收藏/取消；分组自动按主分类归类） ----
const isFav = ref(false)
const favGroup = ref('')
const favBusy = ref(false)

const favoriteGroupLabel = computed(() => favGroup.value || '未分组')

async function toggleFav() {
  if (favBusy.value) return
  favBusy.value = true
  try {
    if (isFav.value) {
      await removeFavorite(projectId.value)
      isFav.value = false
      favGroup.value = ''
    } else {
      await addFavorite(projectId.value)
      isFav.value = true
      favGroup.value = detail.value?.category || '未分组'
    }
  } catch (e) {
    // 静默失败：下次操作重试
  } finally {
    favBusy.value = false
  }
}

// ---- 健康度曲线（更新检查快照：stars 随时间） ----
const healthSnaps = ref<HealthSnapshot[]>([])
const healthLoading = ref(false)

const healthPath = computed(() => {
  const snaps = healthSnaps.value
  if (snaps.length < 2) return ''
  const W = 320
  const H = 64
  const vals = snaps.map((s) => s.stars)
  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const range = Math.max(max - min, 1)
  return snaps
    .map((s, i) => {
      const x = (i / (snaps.length - 1)) * W
      const y = H - 6 - ((s.stars - min) / range) * (H - 12)
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

const healthArea = computed(() => {
  const pts = healthPath.value
  if (!pts) return ''
  return `0,64 ${pts} 320,64`
})

const healthRangeLabel = computed(() => {
  const snaps = healthSnaps.value
  if (snaps.length < 2) return ''
  return `${formatStars(snaps[0].stars)} → ${formatStars(snaps[snaps.length - 1].stars)}`
})

const healthMinMax = computed(() => {
  const snaps = healthSnaps.value
  if (snaps.length < 2) return { min: 0, max: 1 }
  const vals = snaps.map((s) => s.stars)
  return { min: Math.min(...vals), max: Math.max(...vals) }
})

async function loadHealth() {
  if (healthLoading.value) return
  healthLoading.value = true
  try {
    const data = await fetchHealthSnapshots(projectId.value)
    healthSnaps.value = data.items
  } catch { /* 无快照则隐藏曲线 */ } finally {
    healthLoading.value = false
  }
}

// ---- 最新版本说明（release_notes，更新检查时存储） ----
const releaseNotesHtml = computed(() => {
  const notes = detail.value?.release_notes ?? ''
  if (!notes) return ''
  const resolved = resolveReadme(notes, detail.value?.full_name ?? '', detail.value?.html_url ?? '', mirrorPrefix.value)
  return DOMPurify.sanitize(marked.parse(resolved, { async: false }) as string, {
    ADD_ATTR: ['target', 'rel'],
  })
})

function back() {
  // 恢复进入详情页前的列表筛选状态（URL query），避免返回后条件清空
  let query: Record<string, string> = {}
  try {
    const saved = sessionStorage.getItem('listQuery')
    if (saved) query = JSON.parse(saved) as Record<string, string>
  } catch { /* 忽略 */ }
  router.push({ path: '/', query })
}
</script>

<template>
  <div class="container">
    <!-- 顶栏：图形 logo + 项目名（用短名 name，owner 前缀只在加载失败时兜底） -->
    <header class="topbar">
      <button class="btn ghost back-btn" @click="back">← 返回列表</button>
      <h1 class="detail-title">
        <svg class="detail-logo" viewBox="0 0 48 48" aria-hidden="true">
          <path d="M7 31a17 17 0 0 0 34 0" fill="none" stroke="var(--text)" stroke-width="4.5" stroke-linecap="round" />
          <path d="M24 7l2.9 6.6 7.1.6-5.4 4.7 1.6 7-6.2-3.7-6.2 3.7 1.6-7-5.4-4.7 7.1-.6z" fill="var(--accent)" />
        </svg>
        <span class="detail-name">{{ detail?.name || detail?.full_name || '项目详情' }}</span>
      </h1>
      <span v-if="detail" class="stars">★ {{ formatStars(detail.stars) }}</span>
    </header>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="error" class="error-banner">{{ error }}</div>

    <template v-else-if="detail">
      <!-- 简介 -->
      <section class="panel">
        <p class="desc-zh">{{ detail.description || '（无简介）' }}</p>

        <div class="meta-row">
          <span class="tag">{{ detail.category }}</span>
          <span
            v-for="c in extraCategories"
            :key="c"
            class="tag extra-cat"
            :title="`跨界项目，同时属于「${c}」`"
          >{{ c }}</span>
          <span v-if="detail.lang" class="tag" :class="detail.lang === 'zh' ? 'lang-zh' : 'lang-en'">
            {{ detail.lang === 'zh' ? '中文' : '英文' }}
          </span>
          <span v-if="detail.language" class="tag lang">{{ detail.language }}</span>
          <span v-if="detail.zh_friendly" class="tag zh" title="官方提供中文文档或说明（官网/仓库），对中文用户友好">中文友好</span>
          <span v-for="p in detail.platforms" :key="p" class="tag platform">{{ p }}</span>
          <span class="tag">更新 {{ formatDate(detail.pushed_at) }}</span>
        </div>

        <div v-if="detail.system_requirements.length" class="meta-row">
          <span v-for="r in detail.system_requirements" :key="r" class="tag req" :title="r">{{ r }}</span>
        </div>

        <!-- 动作区：主操作居左，辅助动作右对齐；收藏并入行首，不再独占一行 -->
        <div class="detail-actions">
          <button
            class="btn"
            :class="isFav ? 'fav-on' : 'ghost'"
            :disabled="favBusy"
            :title="isFav ? `已归入「${favoriteGroupLabel}」分组` : '收藏后自动按项目主分类归入同组'"
            @click="toggleFav"
          >
            {{ isFav ? '★ 已收藏' : '☆ 收藏' }}
          </button>
          <template v-if="detail.assets.length > 0">
            <div v-if="matchedAssets.length" class="dl-primary">
              <button class="btn dl" @click="downloadBest(matchedAssets[0].url)">
                下载 {{ currentOS }} 版{{ mirrorUrl(matchedAssets[0].url) ? '（镜像加速）' : '' }}
              </button>
              <button v-if="mirrorUrl(matchedAssets[0].url)" class="btn ghost small" :title="matchedAssets[0].url" @click="external(matchedAssets[0].url)">原地址</button>
            </div>
            <button class="btn prompt" title="查看安装提示词全文，复制给豆包等能操作电脑的 AI 助手，让它帮你安装" @click="openInstallPrompt">复制安装提示词</button>
          </template>
          <template v-else-if="!detail.assets_probed">
            <button class="btn dl" @click="openInstallPrompt">复制安装提示词</button>
            <span class="probe-chip" title="正在后台探测 GitHub release，完成后这里会出现下载按钮；无 Token 时限速约 60 个/小时">安装包检测中…</span>
          </template>
          <button v-else class="btn dl" @click="openInstallPrompt">复制安装提示词</button>
          <span class="actions-spacer"></span>
          <button class="btn ghost" title="需可访问 GitHub 的网络；打不开时可直接使用下方站内 README" @click="external(detail.html_url)">
            {{ detail.assets.length > 0 ? 'GitHub 发布页 ↗' : 'GitHub ↗' }}
          </button>
        </div>
        <!-- 安装提示词的说明独占一行，不再插在按钮之间 -->
        <p class="actions-hint">
          <template v-if="detail.assets.length === 0 && !detail.assets_probed">安装包还在检测中？</template>
          <template v-else-if="detail.assets.length === 0">没有现成安装包？</template>
          复制安装提示词发给豆包等 AI 助手（选「本地电脑模式」），它会帮你下载、装好并运行
        </p>
        <p v-if="matchedAssets.length" class="actions-hint">{{ matchedAssets[0].name }}（{{ formatSize(matchedAssets[0].size) }}） · 点击下方列表文件名也可直接下载</p>

        <!-- 全部版本列表 -->
        <div v-if="detail.assets.length > 0" class="asset-list">
          <div class="asset-head">全部版本（{{ detail.assets.length }} 个安装包）</div>
          <div
            v-for="a in detail.assets"
            :key="a.url"
            class="asset-row"
            :class="{ match: a.platform === currentOS }"
            @click="downloadBest(a.url)"
          >
            <span class="asset-platform" :class="{ match: a.platform === currentOS }">
              {{ a.platform || '其他' }}
              <span v-if="a.platform === currentOS" class="pick-tag">当前系统</span>
            </span>
            <span class="asset-name" :title="a.name">{{ a.name }}</span>
            <span class="asset-size">{{ formatSize(a.size) }}</span>
            <button class="btn dl small" @click.stop="downloadBest(a.url)">
              {{ mirrorUrl(a.url) ? '加速下载' : '下载' }}
            </button>
            <button v-if="mirrorUrl(a.url)" class="btn ghost small" :title="a.url" @click.stop="external(a.url)">原地址</button>
          </div>
          <div class="hint">点击文件名可直接下载；文件名可判断架构（x64 / arm64 等）。</div>
        </div>

        <!-- 健康度曲线（更新检查快照） -->
        <div v-if="healthSnaps.length >= 2" class="health-block">
          <div class="health-head">
            <span>健康度（Star 曲线）</span>
            <span class="hint">{{ healthRangeLabel }} · {{ healthSnaps.length }} 个检查快照</span>
          </div>
          <svg class="health-svg" viewBox="0 0 320 64" preserveAspectRatio="none" role="img" aria-label="Star 变化曲线">
            <polygon :points="healthArea" fill="rgba(255,153,0,0.12)"></polygon>
            <polyline :points="healthPath" fill="none" stroke="#ff9900" stroke-width="2" stroke-linejoin="round"></polyline>
            <circle
              v-for="(s, i) in healthSnaps"
              :key="i"
              :cx="(i / (healthSnaps.length - 1)) * 320"
              :cy="64 - 6 - ((s.stars - healthMinMax.min) / Math.max(healthMinMax.max - healthMinMax.min, 1)) * (64 - 12)"
              r="2.5"
              fill="#ff9900"
            >
              <title>{{ formatStars(s.stars) }}（{{ s.checked_at }}）{{ s.has_update ? '· 有新版本 ' + s.latest_tag : '' }}</title>
            </circle>
          </svg>
        </div>
      </section>

      <!-- 最新版本说明 -->
      <section v-if="releaseNotesHtml" class="panel readme-panel">
        <div class="readme-head">
          <span>最新版本说明</span>
        </div>
        <div class="markdown-body" v-html="releaseNotesHtml"></div>
      </section>

      <!-- README -->
      <section class="panel readme-panel">
        <div class="readme-head">
          <span>README</span>
          <div v-if="readmeLangs.length > 1" class="lang-tabs">
            <button
              v-for="o in readmeLangs"
              :key="o.key"
              class="lang-tab"
              :class="{ active: readmeLang === o.key }"
              @click="switchLang(o.key)"
            >{{ o.label }}</button>
          </div>
        </div>
        <div v-if="extraLoading" class="variant-hint">正在从镜像抓取「{{ extraLoading }}」版本…</div>
        <div v-else-if="extraError" class="variant-hint error">
          {{ extraError }}
          <a v-if="extraFallbackUrl" :href="extraFallbackUrl" target="_blank" rel="noopener" @click.prevent="openExternal(extraFallbackUrl)">在 GitHub 打开</a>
        </div>
        <div v-if="readmeHtml" class="markdown-body" v-html="readmeHtml" @click="onReadmeClick"></div>
        <div v-else-if="readmeLang === 'zh'" class="empty">该项目没有自带中文 README。</div>
        <div v-else-if="readmeLang === 'extra'" class="empty">该语言版本内容为空。</div>
        <div v-else class="empty">该项目没有 README 内容。</div>
      </section>

      <!-- 相似项目推荐 -->
      <section v-if="similar.length > 0" class="panel">
        <div class="readme-head">
          <span>相似项目推荐</span>
          <span class="hint">同「{{ detail?.category }}」分类 · star 数量级相近</span>
        </div>
        <div class="similar-grid">
          <router-link
            v-for="s in similar"
            :key="s.id"
            :to="`/project/${s.id}`"
            class="similar-card"
          >
            <div class="similar-name">{{ s.name }}</div>
            <div class="similar-desc">{{ s.description || '（无简介）' }}</div>
            <div class="similar-meta">
              <span class="tag">{{ s.category }}</span>
              <span v-if="s.language" class="tag lang">{{ s.language }}</span>
              <span class="stars">★ {{ formatStars(s.stars) }}</span>
            </div>
          </router-link>
        </div>
      </section>
    </template>

    <InstallPromptModal
      v-if="showInstallPrompt && detail"
      :project="detail"
      :os="currentOS"
      @close="showInstallPrompt = false"
    />
  </div>
</template>
