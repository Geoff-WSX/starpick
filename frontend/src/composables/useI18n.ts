// 界面语言：中/英切换（与主题同机制：localStorage 记忆，全局响应式）
// 语言包在 src/i18n/zh.ts 与 en.ts；t(key) 返回当前语言文案，缺失键回退中文。
import { computed, ref } from 'vue'
import zh from '../i18n/zh'
import en from '../i18n/en'

export type UiLang = 'zh' | 'en'
const LANG_KEY = 'starpick-lang'

const saved = (() => {
  try { return localStorage.getItem(LANG_KEY) } catch { return null }
})()
const lang = ref<UiLang>(saved === 'en' ? 'en' : 'zh')

// 首帧同步 <html lang>：index.html 硬编码 zh-CN，若用户在英语模式刷新页面，
// 不在这里同步就会一直是 zh-CN（影响屏幕阅读器与浏览器翻译提示）。
// setLang 只在「切换时」调 setAttribute，覆盖不到「加载时已是 en」的场景。
if (typeof document !== 'undefined') {
  document.documentElement.setAttribute('lang', lang.value === 'en' ? 'en' : 'zh-CN')
}

export function setLang(l: UiLang) {
  lang.value = l
  try { localStorage.setItem(LANG_KEY, l) } catch { /* 隐私模式忽略 */ }
  // 与 index.html 的 zh-CN 保持同一写法（en 无需区域码）
  document.documentElement.setAttribute('lang', l === 'en' ? 'en' : 'zh-CN')
}

export function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

const dicts: Record<UiLang, Record<string, string>> = { zh, en }

/** 系统要求前缀映射：后端生成的数据是「内存: 4 MiB」中文前缀，英文模式映射 */
export function mapReqLabel(r: string): string {
  const map: [string, string][] = [
    ['内存', 'req_mem'], ['磁盘空间', 'req_disk'], ['GPU/显卡', 'req_gpu'],
    ['显存', 'req_vram'], ['Python 版本', 'req_py'], ['Node.js', 'req_node'],
    ['Docker', 'req_docker'], ['系统', 'req_sys'],
  ]
  for (const [zh, key] of map) {
    if (r.startsWith(zh + ':')) return t(key) + r.slice(zh.length)
  }
  return r
}

export function t(key: string): string {
  const v = dicts[lang.value][key] ?? dicts.zh[key]
  if (v !== undefined) return v
  // 自定义分类没有英文映射：回退显示中文原名（去掉 cat_ 前缀），
  // 而不是返回原始 key「cat_xxx」——见首页筛选 chip 自定义分类场景
  if (key.startsWith('cat_')) return key.slice(4)
  return key
}

/** 注册自定义分类的英文名（来自 /api/categories 的 custom_en）：
 * 英文模式下自定义分类显示其英文关键词（如「测试工具」→ test-tools）。 */
export function registerCustomCategoryEn(mapping: Record<string, string>) {
  for (const [zhName, enName] of Object.entries(mapping)) {
    dicts.en['cat_' + zhName] = enName
  }
}

export const currentLang = computed(() => lang.value)
