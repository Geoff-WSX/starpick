import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

// 主题在挂载前恢复：详情页直开/刷新时不经过 AppHeader，硬编码的 dark 需在这里纠正为 localStorage 记忆值
const THEME_KEY = 'starpick-theme'
let theme: 'dark' | 'light' = 'dark'
try {
  const saved = localStorage.getItem(THEME_KEY)
  if (saved === 'light' || saved === 'dark') theme = saved
} catch { /* 忽略 */ }
document.documentElement.setAttribute('data-theme', theme)

createApp(App).use(router).mount('#app')
