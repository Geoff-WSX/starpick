<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { t } from '../composables/useI18n'

// 通用下拉：自绘菜单（视觉与首页排序下拉一致）
// 稳定性保障：选项点击防冒泡、空间不足向上翻转、滚动仅完全滚出时收起、
// scroll 事件直接挂载到滚动容器（scroll 不冒泡）。
const props = defineProps<{
  modelValue: string | number
  options: { value: string | number; label: string | (() => string) }[]
  disabled?: boolean
}>()
const emit = defineEmits<{ (e: 'update:modelValue', v: string | number): void }>()

const open = ref(false)
// 弹窗（overflow 滚动容器）内空间不足时向上展开，避免菜单被裁剪
const menuUp = ref(false)
const root = ref<HTMLElement | null>(null)
const label = computed(() => {
  const l = props.options.find((o) => o.value === props.modelValue)?.label
  return typeof l === 'function' ? l() : (l ?? '')
})

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value) {
    attachScrollWatch()
    void reposition()
  } else {
    detachScrollWatch()
  }
}

function pick(v: string | number) {
  open.value = false
  detachScrollWatch()
  if (v !== props.modelValue) emit('update:modelValue', v)
}

/** 测量菜单与最近滚动容器：底部放不下则向上展开 */
async function reposition() {
  await nextTick()
  const el = root.value
  if (!el) return
  const menu = el.querySelector<HTMLElement>('.bs-menu')
  if (!menu) return
  const container = el.closest('.collect-modal, .modal, .modal-overlay, .notif-panel')
  const menuRect = menu.getBoundingClientRect()
  if (!container) {
    menuUp.value = menuRect.bottom > window.innerHeight - 8
    return
  }
  const box = container.getBoundingClientRect()
  // 向下展开后若超出容器可视底部 → 改为向上展开
  menuUp.value = menuRect.bottom > box.bottom - 8
}

/** 滚动时：仅当菜单被完全滚出可视区才收起（避免浏览器滚动锚定等微小滚动误关菜单） */
function onScroll(e: Event) {
  if (!open.value) return
  const el = root.value
  if (!el) return
  const menu = el.querySelector<HTMLElement>('.bs-menu')
  if (!menu) return
  const mr = menu.getBoundingClientRect()
  const t = e.target
  let box: DOMRect | null = null
  if (t instanceof HTMLElement) {
    box = t.getBoundingClientRect()
  }
  const outOfViewport = mr.bottom < 0 || mr.top > window.innerHeight
  const outOfBox = box ? mr.bottom < box.top - 4 || mr.top > box.bottom + 4 : false
  if (outOfViewport || outOfBox) {
    open.value = false
    detachScrollWatch()
  }
}

// scroll 事件不冒泡：菜单打开时直接挂到滚动容器（弹窗）与 window 上
let scrollCleanup: (() => void) | null = null
function attachScrollWatch() {
  detachScrollWatch()
  const el = root.value
  if (!el) return
  const container = el.closest<HTMLElement>('.collect-modal, .modal, .modal-overlay, .notif-panel')
  const targets: EventTarget[] = container ? [container] : []
  targets.push(window)
  for (const t of targets) t.addEventListener('scroll', onScroll, true)
  scrollCleanup = () => {
    for (const t of targets) t.removeEventListener('scroll', onScroll, true)
  }
}
function detachScrollWatch() {
  scrollCleanup?.()
  scrollCleanup = null
}

function onDocClick(e: MouseEvent) {
  if (open.value && !(e.target as HTMLElement).closest('.base-select')) {
    open.value = false
    detachScrollWatch()
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => {
  document.removeEventListener('click', onDocClick)
  detachScrollWatch()
})
</script>

<template>
  <div
    ref="root"
    class="base-select"
    :class="{ open, disabled, 'menu-up': menuUp }"
    @click="toggle"
  >
    <span class="bs-value">{{ label || t('bs_placeholder') }}</span>
    <span class="bs-caret" aria-hidden="true"></span>
    <div v-if="open" class="bs-menu">
      <button
        v-for="o in options"
        :key="String(o.value)"
        class="bs-option"
        :class="{ active: o.value === modelValue }"
        @click.stop="pick(o.value)"
      >
        {{ typeof o.label === 'function' ? o.label() : o.label }}
      </button>
    </div>
  </div>
</template>
