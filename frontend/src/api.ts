// 后端 API 类型与封装（仅调用 JSON API，三层单向依赖）

/**
 * 打开外部链接（外链统一切换点）。
 *
 * 桌面窗口（pywebview）里 window.open('_blank') 静默失效——macOS 新 WebView
 * 回调只转发 LinkActivated 导航，window.open 的导航不属于该类型，前端全部
 * 外链（下载/GitHub 页）必须走桌面壳注入的 js_api 桥；浏览器模式没有
 * window.pywebview，自动回退 window.open，行为不变。
 */
export function openExternal(url: string): void {
  if (!url) return
  const bridge = (window as { pywebview?: { api?: { open_external?: (u: string) => unknown } } })
    .pywebview?.api
  if (bridge?.open_external) {
    bridge.open_external(url)
    return
  }
  window.open(url, '_blank', 'noopener')
}

export interface Asset {
  name: string
  url: string
  size: number
  platform: string
}

export interface Project {
  id: number
  full_name: string
  name: string
  owner: string
  description: string
  homepage: string
  stars: number
  language: string
  pushed_at: string
  html_url: string
  topics: string[]
  platforms: string[]
  system_requirements: string[]
  download_url: string
  assets: Asset[]
  /** release 是否已探测过：false 且 assets 为空 =「安装包检测中」态 */
  assets_probed?: boolean
  zh_friendly: boolean
  category: string
  /** 附加分类（跨界项目多分类标注；不含主分类时前端按主分类兜底） */
  categories: string[]
  /** 国内镜像前缀列表：无代理访问 GitHub 时用「镜像前缀 + 原始URL」加速下载/图片 */
  mirror_prefixes: string[]
  lang: string
  first_seen_at: string
  last_recommended_at: string
  /** 最近一轮采集新增入库的项目 */
  is_new: boolean
  /** 是否已收藏（列表接口返回；卡片快速收藏） */
  is_favorite: boolean
  /** 收藏所在分组 */
  favorite_group: string
}

export interface ProjectDetail extends Project {
  readme: string
  /** 项目自带的中文 README 变体（如 README.zh-CN.md）；为空表示项目未提供 */
  readme_zh: string
  /** 项目自带的英文 README 变体（如 README.en.md）；为空表示项目未提供 */
  readme_en: string
  /** 中/英之外的语言变体（语言键 → 正文）：采集时按 README 声明全量入库，点开即显示 */
  readme_variants?: Record<string, string>
  /** 国内镜像前缀列表：README 图片直链在部分网络无法访问时替换为镜像 */
  mirror_prefixes: string[]
  /** 是否已收藏（收藏页 / 详情页收藏按钮） */
  is_favorite: boolean
  /** 收藏所在分组 */
  favorite_group: string
  /** 最新 release 说明（更新检查时存储；GitHub 未提供则为空） */
  release_notes: string
}

export interface ProjectListResponse {
  total: number
  page: number
  page_size: number
  items: Project[]
  /** 新增筛选口径：最近一轮开始时间；为空表示未启用新增筛选 */
  new_since?: string
}

export interface CollectStatus {
  running: boolean
  phase: string
  message: string
  current_category: string
  categories_done: number
  categories_total: number
  enriched_done: number
  enriched_total: number
  backfill_done: number
  backfill_total: number
  started_at: string
  updated_at: string
}

export interface RoundInfo {
  id: number
  started_at: string
  finished_at: string | null
  status: 'success' | 'failed' | 'running' | 'skipped' | string
  /** 每分类新增项目数 */
  categories_summary: Record<string, number>
  /** candidates / inserted / updated / skipped / duration_sec */
  stats: Record<string, number>
  error: string | null
  /** 轮次来源：manual 手动 / scheduled 定时 / startup 启动补跑 */
  source?: string
}

export interface StatsResponse {
  total_projects: number
  /** 已探测到安装包的项目数（assets 回填进度信号） */
  assets_ready: number
  /** 尚未探测 release 的项目数（顶栏「安装包检测 N/M」进度，全部完成归零） */
  assets_pending: number
  recent_rounds: number
  rounds_by_status: Record<string, number>
  categories_summary: Record<string, number>
}

export interface HealthResponse {
  status: string
  version: string
  db_ok: boolean
  last_round: Record<string, unknown> | null
  /** 是否已配置 GitHub Token（决定采集速度与限流） */
  has_token?: boolean
}

async function getJson<T>(url: string): Promise<T> {
  const resp = await fetch(url)
  if (!resp.ok) {
    throw new Error(`请求失败 ${resp.status}: ${url}`)
  }
  return resp.json() as Promise<T>
}

export async function fetchProjects(params: {
  category?: string
  keyword?: string
  lang?: string
  new?: string
  /** 只看「中文友好」标记的项目 */
  zh?: string
  page: number
  page_size: number
  sort: string
}): Promise<ProjectListResponse> {
  const q = new URLSearchParams()
  if (params.category) q.set('category', params.category)
  if (params.keyword) q.set('keyword', params.keyword)
  if (params.lang) q.set('lang', params.lang)
  if (params.new) q.set('new', params.new)
  if (params.zh) q.set('zh', params.zh)
  q.set('page', String(params.page))
  q.set('page_size', String(params.page_size))
  q.set('sort', params.sort)
  return getJson<ProjectListResponse>(`/api/projects?${q.toString()}`)
}

export function fetchCollectStatus(): Promise<CollectStatus> {
  return getJson<CollectStatus>('/api/collect/status')
}

export function fetchRounds(limit = 30): Promise<{ items: RoundInfo[] }> {
  return getJson(`/api/rounds?limit=${limit}`)
}

export function fetchStats(): Promise<StatsResponse> {
  return getJson('/api/stats')
}

export function fetchHealth(): Promise<HealthResponse> {
  return getJson('/health')
}

export function fetchProjectDetail(id: number): Promise<ProjectDetail> {
  return getJson(`/api/projects/${id}`)
}

export interface CollectParams {
  min_stars: number
  max_inactive_days: number
  per_category_max: number
  categories: string[]
}

export async function triggerRun(payload?: {
  min_stars?: number
  max_inactive_days?: number
  per_category_top?: number
  categories?: string[]
  custom_name?: string
  custom_keywords?: string
}): Promise<{ status: string; reason: string }> {
  const resp = await fetch('/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload ?? {}),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `触发采集失败 ${resp.status}`)
  }
  return resp.json()
}

export function fetchCollectParams(): Promise<CollectParams> {
  return getJson('/api/collect/params')
}

export function fetchCategories(): Promise<{ categories: string[]; counts?: Record<string, number> }> {
  return getJson('/api/categories')
}

// ---- 分类清除（删除选中分类的存量项目，收藏保留；采集运行中后端拒绝） ----
export interface CategoryCleanupCounts {
  counts: Record<string, number>
  /** 采集任务运行中（后端将拒绝执行删除） */
  running: boolean
}

export async function previewCategoryCleanup(names: string[]): Promise<CategoryCleanupCounts> {
  return postJson('/api/categories/preview-cleanup', { names })
}

export async function cleanupCategories(names: string[]): Promise<{ deleted: number; kept_favorites: number }> {
  return postJson('/api/categories/cleanup', { names })
}

async function postJson<T>(url: string, body: unknown): Promise<T> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!resp.ok) {
    let detail = `${resp.status}`
    try {
      const err = await resp.json()
      detail = err?.detail || detail
    } catch { /* 非 JSON 错误体 */ }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

// 展示辅助：star 数与 GitHub 展示一致（≥1000 用 k 缩写，不用「万」）
export function formatStars(n: number): string {
  if (n < 1000) return String(n)
  const k = n / 1000
  if (k >= 100) return `${Math.round(k)}k` // 392k
  return `${k.toFixed(1).replace(/\.0$/, '')}k` // 3.2k / 32k / 32.7k
}

export function formatDate(iso: string): string {
  if (!iso) return '—'
  return iso.slice(0, 10)
}

/** 按 UA 推断当前操作系统（下载版本选择默认项）。 */
export function detectOS(): string {
  const ua = navigator.userAgent
  if (/Windows/i.test(ua)) return 'Windows'
  if (/Mac/i.test(ua)) return 'macOS'
  if (/Android/i.test(ua)) return 'Android'
  if (/iPhone|iPad|iPod/i.test(ua)) return 'iOS'
  if (/Linux/i.test(ua)) return 'Linux'
  return ''
}

export function formatSize(bytes: number): string {
  if (!bytes) return ''
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${bytes} B`
}


// ---------------- 更新检查 ----------------
export interface UpdateStatus {
  running: boolean
  phase: string
  message: string
  done: number
  total: number
  updated: number
  nochange: number
  failed: number
  started_at: string
  updated_at: string
}

export interface UpdateRound {
  id: number
  started_at: string
  finished_at: string
  status: string
  scope: string
  stats: Record<string, unknown>
  error: string
}

export interface UpdateSummary {
  total_projects: number
  projects_with_update: number
  last_check: UpdateRound | null
}

export function fetchUpdateStatus(): Promise<UpdateStatus> {
  return getJson('/api/updates/status')
}

export function fetchUpdateRounds(limit = 30): Promise<{ items: UpdateRound[] }> {
  return getJson(`/api/update-rounds?limit=${limit}`)
}

export function fetchUpdateSummary(): Promise<UpdateSummary> {
  return getJson('/api/updates/summary')
}

export async function triggerUpdates(categories?: string[]): Promise<{ status: string; mode?: string; reason?: string }> {
  const qs = categories && categories.length
    ? '?' + categories.map((c) => `category=${encodeURIComponent(c)}`).join('&')
    : ''
  const resp = await fetch(`/api/updates/run${qs}`, { method: 'POST' })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}


// ---------------- 收藏 + 健康度 ----------------
export interface FavoriteOut {
  project_id: number
  group_name: string
  favorited_at: string
  full_name: string
  name: string
  owner: string
  description: string
  stars: number
  language: string
  category: string
  has_update: boolean
  latest_tag: string
  last_check_at: string
}

export interface FavoriteGroups {
  group_name: string
  cnt: number
}

export interface HealthSnapshot {
  stars: number
  has_update: number
  latest_tag: string
  checked_at: string
}

export async function addFavorite(projectId: number): Promise<{ status: string; group_name: string }> {
  const resp = await fetch(`/api/favorites/${projectId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

export async function removeFavorite(projectId: number): Promise<{ status: string }> {
  const resp = await fetch(`/api/favorites/${projectId}`, { method: 'DELETE' })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

export async function moveFavorite(projectId: number, groupName: string): Promise<{ status: string; group_name: string }> {
  const resp = await fetch(`/api/favorites/${projectId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_name: groupName }),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

export function fetchFavorites(group = ''): Promise<{ items: FavoriteOut[]; groups: FavoriteGroups[] }> {
  return getJson(`/api/favorites${group ? `?group=${encodeURIComponent(group)}` : ''}`)
}

export function fetchHealthSnapshots(projectId: number): Promise<{ items: HealthSnapshot[] }> {
  return getJson(`/api/projects/${projectId}/health`)
}


// ---------------- 通知中心 + 每日精选 ----------------
export interface NotificationItem {
  id: number
  type: string
  project_id: number
  title: string
  message: string
  created_at: string
  read_at: string
}

export interface DailyPickItem {
  id: number
  full_name: string
  name: string
  owner: string
  description: string
  stars: number
  language: string
  category: string
  zh_friendly: number
  rank: number
  score: number
}

export interface DailyPick {
  pick_date: string
  items: DailyPickItem[]
}

export function fetchNotifications(limit = 30): Promise<{ items: NotificationItem[]; unread_count: number }> {
  return getJson(`/api/notifications?limit=${limit}`)
}

export async function markNotificationsRead(ids: number[] = []): Promise<{ status: string; updated: number }> {
  const resp = await fetch('/api/notifications/mark-read', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

export function fetchDailyPick(): Promise<DailyPick> {
  return getJson('/api/picks/latest')
}

export function fetchShufflePick(): Promise<DailyPick> {
  return getJson('/api/picks/shuffle')
}

/** 相似项目推荐：同分类 + star 数量级相近（详情页继续探索） */
export function fetchSimilarProjects(projectId: number): Promise<{ items: Project[] }> {
  return getJson(`/api/projects/${projectId}/similar`)
}

/** 按需抓取 README 语言变体（README 内语言切换链接未预先采集时；仅本仓库变体路径） */
export function fetchReadmeVariant(
  projectId: number,
  path: string,
): Promise<{ path: string; content: string; truncated: boolean }> {
  return getJson(`/api/projects/${projectId}/readme-variant?path=${encodeURIComponent(path)}`)
}


// ---- 设置：GitHub Token 热生效 + 「关于」分组 ----
export interface AppUpdateState {
  /** none=尚未检查 / up_to_date=已是最新 / newer=发现新版本 / unknown=无法检查（网络或私有仓库） */
  status: 'none' | 'up_to_date' | 'newer' | 'unknown'
  latest_tag: string
  published_at: string
  checked_at: string
}

export interface SettingsState {
  has_token: boolean
  masked_token: string
  search_rate_per_minute: number
  core_rate_per_hour: number
  version: string
  repo_url: string
  app_update: AppUpdateState
}

export function fetchSettings(): Promise<SettingsState> {
  return getJson('/api/settings')
}

/** 手动触发应用更新检查（后台执行立即返回；结果稍后由 fetchSettings 轮询读取） */
export async function checkAppUpdate(): Promise<void> {
  const resp = await fetch('/api/app/update-check', { method: 'POST' })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
}

export async function saveToken(token: string): Promise<SettingsState> {
  const resp = await fetch('/api/settings/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}

export async function clearToken(): Promise<SettingsState> {
  const resp = await fetch('/api/settings/token/clear', { method: 'POST' })
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new Error(body?.detail ?? `HTTP ${resp.status}`)
  }
  return resp.json()
}
