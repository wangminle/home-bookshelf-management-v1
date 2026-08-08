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

const placeholderColor = computed(() => {
  // 根据书名生成稳定的色相
  const hash = props.book.title.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  const hue = hash % 360
  return `hsl(${hue}, 45%, 55%)`
})
</script>

<template>
  <img v-if="url" :src="url" class="book-cover" loading="lazy" :alt="book.title" />
  <div v-else class="book-placeholder" :style="{ background: placeholderColor }">
    {{ placeholderChar }}
  </div>
</template>
