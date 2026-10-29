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
  return dicts[lang.value][key] ?? dicts.zh[key] ?? key
}

export const currentLang = computed(() => lang.value)
