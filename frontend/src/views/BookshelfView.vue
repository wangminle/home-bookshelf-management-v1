<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { useBooks } from '@/composables/useBooks'
import { useApiStore } from '@/stores/api'
import BookCover from '@/components/BookCover.vue'
import { READING_STATUSES } from '@/types/models'
import type { StatsOut, CategoryCount } from '@/types/models'

const { books, total, loading, hasMore, filters, loadInitial, loadMore } = useBooks()
const api = useApiStore()

const categories = ref<CategoryCount[]>([])

// keyword debounce
let keywordTimer: ReturnType<typeof setTimeout> | null = null
const keywordInput = ref('')
watch(keywordInput, (val) => {
  if (keywordTimer) clearTimeout(keywordTimer)
  keywordTimer = setTimeout(() => {
    filters.keyword = val
  }, 300)
})

async function loadCategories() {
  try {
    const stats = await api.get<StatsOut>('/stats')
    categories.value = stats.by_category
  } catch {
    // 静默
  }
}

function handleScroll() {
  const scrolled = window.scrollY + window.innerHeight
  const total2 = document.documentElement.scrollHeight
  if (scrolled >= total2 - 200) {
    loadMore()
  }
}

onMounted(() => {
  loadInitial()
  loadCategories()
  window.addEventListener('scroll', handleScroll)
})
</script>

<template>
  <div>
    <div class="filter-bar">
      <input
        v-model="keywordInput"
        type="text"
        placeholder="搜索书名..."
      />
      <select v-model="filters.status">
        <option v-for="s in READING_STATUSES" :key="s.value" :value="s.value">
          {{ s.label }}
        </option>
      </select>
      <select v-model="filters.category">
        <option value="">全部分类</option>
        <option v-for="c in categories" :key="c.category" :value="c.category">
          {{ c.category }} ({{ c.count }})
        </option>
      </select>
      <span style="color: var(--text-muted); font-size: 13px;">共 {{ total }} 本</span>
    </div>

    <div v-if="loading && books.length === 0" class="loading">加载中...</div>

    <div v-else-if="books.length === 0" class="empty">书架空空如也，用 CLI 或 Agent 入库吧</div>

    <div v-else class="book-grid">
      <RouterLink
        v-for="book in books"
        :key="book.id"
        :to="`/books/${book.id}`"
        class="book-card"
      >
        <BookCover :book="book" />
        <div class="book-info">
          <div class="book-title">{{ book.title }}</div>
          <div v-if="book.authors?.length" class="book-author">
            {{ book.authors.join(', ') }}
          </div>
        </div>
      </RouterLink>
    </div>

    <div v-if="loading && books.length > 0" class="loading" style="padding: 20px;">
      加载更多...
    </div>
    <div v-if="!hasMore && books.length > 0" class="loading" style="padding: 20px; font-size: 13px;">
      已显示全部
    </div>
  </div>
</template>
