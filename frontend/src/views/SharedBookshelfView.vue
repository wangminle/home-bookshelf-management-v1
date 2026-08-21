<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  publicCatalogSearch,
  publicCoverUrl,
  type PublicCatalogBook,
} from '@/stores/api'

/**
 * 匿名共享书架（权限阶段 1：C 模式）。
 * 只消费 Public Catalog API（L1 白名单字段）；不可信来源/关闭模式时
 * 自动降级为提示 + 登录入口（基线 §2.1/§10.1）。
 */
const status = ref<'loading' | 'ok' | 'lan_required' | 'disabled' | 'error' | 'rate_limited'>('loading')
const items = ref<PublicCatalogBook[]>([])
const total = ref(0)
const page = ref(1)
const hasMore = ref(false)
const loadingMore = ref(false)
const selected = ref<PublicCatalogBook | null>(null)

const query = ref('')
const category = ref('')
const availability = ref('')

const AVAILABILITY_LABELS: Record<string, string> = {
  in_shelf: '在架',
  borrowed: '外借',
  unknown: '—',
}

async function load(nextPage = 1) {
  if (nextPage === 1) status.value = 'loading'
  const result = await publicCatalogSearch({
    query: query.value || undefined,
    category: category.value || undefined,
    availability: availability.value || undefined,
    page: nextPage,
  })
  if (!result.ok) {
    if (result.code === 'LAN_REQUIRED') status.value = 'lan_required'
    else if (result.code === 'ANONYMOUS_CATALOG_DISABLED') status.value = 'disabled'
    else if (result.code === 'RATE_LIMITED') status.value = 'rate_limited'
    else status.value = 'error'
    return
  }
  status.value = 'ok'
  total.value = result.data.total
  hasMore.value = result.data.has_more
  page.value = result.data.page
  items.value = nextPage === 1 ? result.data.items : [...items.value, ...result.data.items]
}

async function loadMore() {
  if (!hasMore.value || loadingMore.value) return
  loadingMore.value = true
  try {
    await load(page.value + 1)
  } finally {
    loadingMore.value = false
  }
}

function onSearch() {
  selected.value = null
  load(1)
}

function onReset() {
  query.value = ''
  category.value = ''
  availability.value = ''
  onSearch()
}

onMounted(() => load(1))
</script>

<template>
  <section class="shared-view">
    <div class="shared-header">
      <div>
        <h1>共享书架</h1>
        <p class="shared-note">局域网共享视图 · 仅展示书目公开信息</p>
      </div>
      <RouterLink to="/login" class="login-link">登录管理</RouterLink>
    </div>

    <!-- 降级状态：不可信来源 / 模式关闭 / 限流 -->
    <div v-if="status === 'lan_required'" class="gate-card" role="status">
      <p class="gate-title">共享书架仅在可信家庭局域网内开放</p>
      <p>当前无法确认请求来自家庭局域网，已自动切换为登录模式。</p>
      <RouterLink to="/login" class="login-link">前往登录</RouterLink>
    </div>
    <div v-else-if="status === 'disabled'" class="gate-card" role="status">
      <p class="gate-title">匿名浏览未开启</p>
      <p>Owner 可在部署配置中将 ANONYMOUS_CATALOG_MODE 设为 lan_shared 开启局域网共享。</p>
      <RouterLink to="/login" class="login-link">前往登录</RouterLink>
    </div>
    <div v-else-if="status === 'rate_limited'" class="gate-card" role="status">
      <p class="gate-title">浏览过于频繁</p>
      <p>请稍后再试。</p>
    </div>
    <div v-else-if="status === 'error'" class="gate-card" role="alert">
      <p class="gate-title">加载失败</p>
      <button class="login-link" @click="load(1)">重试</button>
    </div>

    <template v-else>
      <form class="filters" @submit.prevent="onSearch">
        <input v-model="query" type="search" placeholder="书名 / 作者 / ISBN" aria-label="搜索书目" />
        <input v-model="category" type="search" placeholder="分类" aria-label="按分类筛选" />
        <select v-model="availability" aria-label="按库存状态筛选">
          <option value="">全部状态</option>
          <option value="in_shelf">在架</option>
          <option value="borrowed">外借</option>
        </select>
        <button type="submit">搜索</button>
        <button type="button" class="ghost" @click="onReset">重置</button>
      </form>

      <p v-if="status === 'ok'" class="result-meta">共 {{ total }} 本</p>
      <p v-else class="result-meta">加载中…</p>

      <div v-if="status === 'ok' && items.length === 0" class="gate-card">
        <p class="gate-title">没有匹配的书目</p>
      </div>

      <ul class="book-grid" aria-label="共享书目列表">
        <li v-for="book in items" :key="book.id">
          <button class="book-card" type="button" @click="selected = book">
            <img
              v-if="publicCoverUrl(book.cover_thumbnail_url)"
              :src="publicCoverUrl(book.cover_thumbnail_url)!"
              :alt="`《${book.title}》封面`"
              loading="lazy"
            />
            <div v-else class="cover-placeholder" aria-hidden="true">📖</div>
            <span class="book-title">{{ book.title }}</span>
            <span class="book-authors">{{ book.authors.join(' / ') || '—' }}</span>
            <span class="badge" :data-av="book.availability_status">
              {{ AVAILABILITY_LABELS[book.availability_status] || '—' }}
            </span>
          </button>
        </li>
      </ul>

      <button v-if="hasMore" class="load-more" :disabled="loadingMore" @click="loadMore">
        {{ loadingMore ? '加载中…' : '加载更多' }}
      </button>

      <!-- 脱敏详情（L1 白名单字段） -->
      <div v-if="selected" class="detail-card" role="dialog" aria-label="书目详情">
        <button class="close" type="button" aria-label="关闭详情" @click="selected = null">×</button>
        <h2>{{ selected.title }}</h2>
        <p v-if="selected.subtitle" class="muted">{{ selected.subtitle }}</p>
        <dl>
          <dt>作者</dt><dd>{{ selected.authors.join(' / ') || '—' }}</dd>
          <template v-if="selected.translators.length">
            <dt>译者</dt><dd>{{ selected.translators.join(' / ') }}</dd>
          </template>
          <dt>出版社</dt><dd>{{ selected.publisher || '—' }}</dd>
          <dt>出版日期</dt><dd>{{ selected.publish_date || '—' }}</dd>
          <dt>分类</dt><dd>{{ selected.category || '—' }}</dd>
          <dt>语言</dt><dd>{{ selected.language || '—' }}</dd>
          <dt>页数</dt><dd>{{ selected.page_count ?? '—' }}</dd>
          <dt>库存状态</dt>
          <dd>{{ AVAILABILITY_LABELS[selected.availability_status] || '—' }}</dd>
          <template v-if="selected.public_tags.length">
            <dt>标签</dt><dd>{{ selected.public_tags.join('、') }}</dd>
          </template>
        </dl>
        <p v-if="selected.summary" class="summary">{{ selected.summary }}</p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.shared-view { max-width: 1200px; margin: 0 auto; padding: var(--space-3, 12px); }
.shared-header { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: var(--space-3, 12px); flex-wrap: wrap; }
.shared-header h1 { font-size: 1.5rem; margin: 0; }
.shared-note { margin: 4px 0 0; color: var(--text-muted, #5a6878); font-size: 0.9rem; }
.login-link { display: inline-block; padding: 8px 16px; border-radius: 8px; background: var(--primary, #2c7a7b); color: #fff; text-decoration: none; border: none; cursor: pointer; font-size: 0.95rem; }
.gate-card { background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 12px; padding: 32px; text-align: center; margin: 48px auto; max-width: 480px; }
.gate-title { font-weight: 600; margin-bottom: 8px; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.filters input, .filters select { padding: 8px 12px; border-radius: 8px; border: 1px solid var(--border, #d7dee8); background: var(--card-bg, #fff); color: inherit; }
.filters button { padding: 8px 16px; border-radius: 8px; border: none; background: var(--primary, #2c7a7b); color: #fff; cursor: pointer; }
.filters button.ghost { background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit; }
.result-meta { color: var(--text-muted, #5a6878); font-size: 0.9rem; }
.book-grid { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: var(--space-3, 12px); }
.book-card { display: flex; flex-direction: column; gap: 6px; padding: var(--space-2, 8px); background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 12px; cursor: pointer; text-align: left; color: inherit; min-height: 0; }
.book-card img, .cover-placeholder { width: 100%; aspect-ratio: 3/4; object-fit: cover; border-radius: 8px; background: var(--cover-light, #d9d2c5); display: flex; align-items: center; justify-content: center; font-size: 2rem; }
.book-title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.book-authors { color: var(--text-muted, #5a6878); font-size: 0.85rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.badge { align-self: flex-start; font-size: 0.75rem; padding: 2px 8px; border-radius: 999px; background: var(--border, #e2e8f0); }
.badge[data-av='in_shelf'] { background: #d9f0e5; color: #1c6b48; }
.badge[data-av='borrowed'] { background: #fdeeda; color: #8a5a19; }
.load-more { display: block; margin: 16px auto; padding: 10px 24px; border-radius: 8px; border: 1px solid var(--border, #d7dee8); background: var(--card-bg, #fff); color: inherit; cursor: pointer; }
.detail-card { position: relative; margin-top: 16px; background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 12px; padding: 20px 24px; }
.detail-card .close { position: absolute; top: 8px; right: 12px; border: none; background: none; font-size: 1.4rem; cursor: pointer; color: inherit; }
.detail-card h2 { margin: 0 0 4px; }
.detail-card dl { display: grid; grid-template-columns: max-content 1fr; gap: 4px 16px; margin: 12px 0; }
.detail-card dt { color: var(--text-muted, #5a6878); }
.detail-card dd { margin: 0; }
.muted { color: var(--text-muted, #5a6878); }
.summary { line-height: 1.6; }
</style>
