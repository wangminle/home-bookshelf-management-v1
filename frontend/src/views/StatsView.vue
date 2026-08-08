<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useApiStore } from '@/stores/api'
import { READING_STATUSES } from '@/types/models'
import type { StatsOut } from '@/types/models'

const api = useApiStore()
const stats = ref<StatsOut | null>(null)
const loading = ref(true)

const maxCategoryCount = computed(() => {
  if (!stats.value || stats.value.by_category.length === 0) return 1
  return stats.value.by_category[0].count
})

async function load() {
  loading.value = true
  try {
    stats.value = await api.get<StatsOut>('/stats')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h2 style="margin-bottom: 20px;">藏书统计</h2>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="stats">
      <!-- 数字卡片 -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{{ stats.total_books }}</div>
          <div class="stat-label">总藏书</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.by_status.reading || 0 }}</div>
          <div class="stat-label">在读</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.by_status.finished || 0 }}</div>
          <div class="stat-label">已读完</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">¥{{ stats.total_spent.toFixed(2) }}</div>
          <div class="stat-label">花费总额 ({{ stats.purchase_count }} 笔)</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.reading_logs_pages_total }}</div>
          <div class="stat-label">累计阅读页数</div>
        </div>
      </div>

      <!-- 状态分布 -->
      <h3 style="margin-bottom: 12px;">阅读状态分布</h3>
      <div style="margin-bottom: 24px;">
        <div v-for="s in READING_STATUSES.filter(s => s.value)" :key="s.value" class="category-bar">
          <span class="category-bar-label">{{ s.label }}</span>
          <div class="category-bar-track">
            <div
              class="category-bar-fill"
              :style="{ width: ((stats.by_status[s.value] || 0) / Math.max(stats.total_books, 1) * 100) + '%' }"
            ></div>
          </div>
          <span style="width: 30px; font-size: 13px;">{{ stats.by_status[s.value] || 0 }}</span>
        </div>
      </div>

      <!-- 分类分布 -->
      <h3 style="margin-bottom: 12px;">分类分布</h3>
      <div style="margin-bottom: 24px;">
        <div v-for="c in stats.by_category" :key="c.category" class="category-bar">
          <span class="category-bar-label">{{ c.category }}</span>
          <div class="category-bar-track">
            <div
              class="category-bar-fill"
              :style="{ width: (c.count / maxCategoryCount * 100) + '%' }"
            ></div>
          </div>
          <span style="width: 30px; font-size: 13px;">{{ c.count }}</span>
        </div>
        <p v-if="stats.by_category.length === 0" style="color: var(--text-muted); font-size: 14px;">
          暂无分类数据
        </p>
      </div>

      <!-- 成员统计 -->
      <h3 style="margin-bottom: 12px;">成员阅读统计</h3>
      <table class="data-table">
        <thead>
          <tr><th>成员</th><th>在读</th><th>已读完</th><th>连续阅读天数</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in stats.members" :key="m.id">
            <td>{{ m.name }}</td>
            <td>{{ m.books_reading }}</td>
            <td>{{ m.books_finished }}</td>
            <td>🔥 {{ m.reading_streak }} 天</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
