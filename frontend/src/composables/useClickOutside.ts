// 点击容器外部时触发回调：popover / 下拉类弹层的通用「点外部关闭」逻辑。
//
// 约定：container 传「触发按钮 + 弹层」的共同父元素（如 .bell-wrap），
// 这样点击触发按钮时不会触发 onOutside，避免与按钮自身的 toggle 相互抖动。
import { onMounted, onUnmounted, type Ref } from 'vue'

export function useClickOutside(
  container: Ref<HTMLElement | null>,
  onOutside: () => void,
) {
  function onDocClick(e: MouseEvent) {
    const el = container.value
    if (!el) return
    const target = e.target as Node | null
    if (target && !el.contains(target)) onOutside()
  }
  onMounted(() => document.addEventListener('click', onDocClick))
  onUnmounted(() => document.removeEventListener('click', onDocClick))
}
