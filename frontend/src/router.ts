import { createRouter, createWebHistory } from 'vue-router'
import ProjectList from './views/ProjectList.vue'
import ProjectDetail from './views/ProjectDetail.vue'


const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'list', component: ProjectList },
    { path: '/project/:id(\\d+)', name: 'detail', component: ProjectDetail, props: true },
    { path: '/collect', redirect: '/' },
    { path: '/favorites', redirect: '/' },
    { path: '/rounds', redirect: '/' },
    { path: '/updates', redirect: '/' },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})

// 浏览器标签页标题固定为品牌名（index.html 的 <title> 与此一致，无需按路由切换）
router.afterEach(() => {
  document.title = 'STARPICK 拾星'
})

export default router
