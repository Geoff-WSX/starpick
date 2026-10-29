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

export function setLang(l: UiLang) {
  lang.value = l
  try { localStorage.setItem(LANG_KEY, l) } catch { /* 隐私模式忽略 */ }
  document.documentElement.setAttribute('lang', l)
}

export function toggleLang() {
  setLang(lang.value === 'zh' ? 'en' : 'zh')
}

const dicts: Record<UiLang, Record<string, string>> = { zh, en }

export function t(key: string): string {
  const v = dicts[lang.value][key] ?? dicts.zh[key]
  if (v !== undefined) return v
  // 自定义分类没有英文映射：回退显示中文原名（去掉 cat_ 前缀），
  // 而不是返回原始 key「cat_xxx」——见首页筛选 chip 自定义分类场景
  if (key.startsWith('cat_')) return key.slice(4)
  return key
}

export const currentLang = computed(() => lang.value)
