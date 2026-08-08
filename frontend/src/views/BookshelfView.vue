<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
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

// 修复 P1：滚动监听使用 requestAnimationFrame 节流，避免高频回流
let ticking = false
function handleScroll() {
  if (ticking) return
  ticking = true
  requestAnimationFrame(() => {
    const scrolled = window.scrollY + window.innerHeight
    const totalHeight = document.documentElement.scrollHeight
    if (scrolled >= totalHeight - 200) {
      loadMore()
    }
    // 回到顶部按钮显隐
    showBackToTop.value = window.scrollY > 600
    ticking = false
  })
}

// 修复 P3：回到顶部按钮
const showBackToTop = ref(false)
function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

onMounted(() => {
  loadInitial()
  loadCategories()
  window.addEventListener('scroll', handleScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', handleScroll)
})
</script>

<template>
  <div>
    <h1 class="sr-only">书架</h1>
    <div class="filter-bar">
      <label class="sr-only" for="search-input">搜索书名</label>
      <input
        id="search-input"
        v-model="keywordInput"
        type="text"
        placeholder="搜索书名..."
      />
      <label class="sr-only" for="status-filter">按阅读状态筛选</label>
      <select id="status-filter" v-model="filters.status">
        <option v-for="s in READING_STATUSES" :key="s.value" :value="s.value">
          {{ s.label }}
        </option>
      </select>
      <label class="sr-only" for="category-filter">按分类筛选</label>
      <select id="category-filter" v-model="filters.category">
        <option value="">全部分类</option>
        <option v-for="c in categories" :key="c.category" :value="c.category">
          {{ c.category }} ({{ c.count }})
        </option>
      </select>
      <span class="filter-count">共 {{ total }} 本</span>
    </div>

    <!-- 骨架屏（修复 P2） -->
    <div v-if="loading && books.length === 0" class="skeleton-grid" aria-hidden="true">
      <div v-for="n in 20" :key="n" class="skeleton-card">
        <div class="skeleton-cover"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
      </div>
    </div>

    <!-- 空状态（修复 4.1：增加引导 · D9：增强视觉引导） -->
    <div v-else-if="books.length === 0" class="empty empty-shelf">
      <div class="empty-icon" aria-hidden="true">📖</div>
      <div class="empty-title">书架空空如也</div>
      <div class="empty-hint">使用 CLI（<code>bookshelf add</code>）或 Agent 入库你的第一本书</div>
    </div>

    <div v-else class="book-grid">
      <RouterLink
        v-for="(book, index) in books"
        :key="book.id"
        :to="`/books/${book.id}`"
        class="book-card"
        :style="{ animationDelay: `${Math.min(index * 30, 600)}ms` }"
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

    <div v-if="loading && books.length > 0" class="loading-more">加载更多...</div>
    <div v-if="!hasMore && books.length > 0" class="loading-more">已显示全部</div>

    <!-- 回到顶部（修复 P3） -->
    <button
      v-if="showBackToTop"
      class="back-to-top"
      aria-label="回到顶部"
      title="回到顶部"
      @click="scrollToTop"
    >
      ↑
    </button>
  </div>
</template>

<style scoped>
/* 屏幕阅读器专用：视觉隐藏但可读 */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* D9：空状态视觉增强 */
.empty-shelf {
  text-align: center;
  padding: 60px 20px;
}
.empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
  opacity: 0.5;
}
.empty-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 8px;
  color: var(--text);
}
</style>
