<script setup lang="ts">
import { computed } from 'vue'
import type { BookOut } from '@/types/models'
import { coverUrl } from '@/stores/api'

const props = defineProps<{ book: BookOut }>()

const url = computed(() => coverUrl(props.book.cover_path))

// 封面占位符：取书名首字符，生成确定性渐变色
const placeholderChar = computed(() => {
  const title = props.book.title || '?'
  return title.charAt(0)
})

// 修复 P2：饱和度提升（45%→62%），通过 CSS 变量 hue 传入，
// 实际 sat/light 由 main.css 的 --cover-sat / --cover-light 决定，
// 暗色模式下自动降低亮度（修复 2.2）。
const placeholderHue = computed(() => {
  const hash = props.book.title.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return hash % 360
})
</script>

<template>
  <img v-if="url" :src="url" class="book-cover" loading="lazy" :alt="book.title" />
  <!-- 修复 P2：占位符添加 role=img + aria-label，屏幕阅读器可识别 -->
  <div
    v-else
    class="book-placeholder"
    :style="{ '--cover-hue': placeholderHue }"
    role="img"
    :aria-label="`${book.title}（无封面）`"
  >
    {{ placeholderChar }}
  </div>
</template>

<style scoped>
.book-placeholder {
  background: hsl(var(--cover-hue, 0), var(--cover-sat, 62%), var(--cover-light, 36%));
}
</style>
