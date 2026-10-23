<script setup lang="ts">
// 分类清除弹窗：多选分类 → 预览可删数量 → 确认删除（收藏保留；采集运行中后端拒绝）
import { computed, ref, watch } from 'vue'
import {
  fetchCategories,
  previewCategoryCleanup,
  cleanupCategories,
} from '../api'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (e: 'close'): void }>()

const categories = ref<string[]>([])
const counts = ref<Record<string, number>>({})
const checked = ref<Set<string>>(new Set())
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const done = ref<{ deleted: number; kept_favorites: number } | null>(null)

// 采集运行中（打开弹窗时快照；后端执行时还会再拒一次）
const running = ref(false)

const totalSelected = computed(() =>
  [...checked.value].reduce((sum, c) => sum + (counts.value[c] ?? 0), 0),
)

async function load() {
  loading.value = true
  error.value = ''
  done.value = null
  checked.value = new Set()
  counts.value = {}
  try {
    const cats = await fetchCategories()
    const names = (cats.categories ?? []).filter(Boolean)
    categories.value = names
    const preview = await previewCategoryCleanup(names)
    counts.value = preview.counts
    running.value = preview.running
  } catch (e) {
    error.value = e instanceof Error ? e.message : '加载分类数据失败'
  } finally {
    loading.value = false
  }
}

function toggle(name: string) {
  const next = new Set(checked.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  checked.value = next
}

async function run() {
  if (!checked.value.size || busy.value) return
  const names = [...checked.value]
  if (!confirm(`确定清除分类「${names.join('、')}」共 ${totalSelected.value} 个项目？\n收藏的项目会保留；删除前会自动备份数据库。`)) return
  busy.value = true
  error.value = ''
  try {
    done.value = await cleanupCategories(names)
    checked.value = new Set()
    // 重新预览（数量已变化）
    const preview = await previewCategoryCleanup(categories.value)
    counts.value = preview.counts
    running.value = preview.running
  } catch (e) {
    error.value = e instanceof Error ? e.message : '清除失败'
  } finally {
    busy.value = false
  }
}

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
    <div class="modal modal-cleanup">
      <div class="modal-head">
        <div class="modal-title">
          <h2>清除分类项目</h2>
          <span class="hint">删除选中分类的已入库项目；收藏的项目会保留，删除前自动备份数据库</span>
        </div>
        <button class="modal-close" title="关闭" @click="close">
          <svg class="modal-close-svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="error && categories.length === 0" class="error-banner">{{ error }}</div>
      <template v-else>
        <div v-if="running" class="cleanup-warn">采集任务运行中，此时无法清除——请等本轮采集结束后再试。</div>

        <div class="cleanup-list">
          <label
            v-for="c in categories"
            :key="c"
            class="cleanup-row"
            :class="{ disabled: running || !(counts[c] > 0) }"
          >
            <input
              type="checkbox"
              :checked="checked.has(c)"
              :disabled="running || !(counts[c] > 0)"
              @change="toggle(c)"
            />
            <span class="cleanup-name">{{ c }}</span>
            <span class="cleanup-count">{{ counts[c] ?? 0 }} 个</span>
          </label>
          <div v-if="categories.every((c) => !(counts[c] > 0))" class="empty">
            每个分类都没有可删的项目（收藏的项目不会被计入）。
          </div>
        </div>

        <div v-if="error" class="error-banner">{{ error }}</div>
        <div v-if="done" class="cleanup-done">
          ✓ 已删除 {{ done.deleted }} 个项目<span v-if="done.kept_favorites > 0">，{{ done.kept_favorites }} 个收藏项目已保留</span>
        </div>

        <div class="modal-foot">
          <span class="hint">{{ checked.size ? `已选 ${checked.size} 个分类，共 ${totalSelected} 个项目` : '勾选要清除的分类' }}</span>
          <button class="btn danger" :disabled="!checked.size || busy || running" @click="run">
            {{ busy ? '清除中…' : `清除选中分类${totalSelected ? `（${totalSelected}）` : ''}` }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>
