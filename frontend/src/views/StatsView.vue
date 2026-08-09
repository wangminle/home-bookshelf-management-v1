<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useApiStore } from '@/stores/api'
import { READING_STATUSES } from '@/types/models'
import type { StatsOut } from '@/types/models'

const api = useApiStore()
const stats = ref<StatsOut | null>(null)
const loading = ref(true)
const loadError = ref<string | null>(null)  // BUG-125：加载失败时展示错误状态 + 重试

const maxCategoryCount = computed(() => {
  if (!stats.value || stats.value.by_category.length === 0) return 1
  return stats.value.by_category[0].count
})

const maxYearlySpent = computed(() => {
  if (!stats.value || stats.value.by_year.length === 0) return 0
  return Math.max(...stats.value.by_year.map((y) => y.spent))
})

// 修复 BUG：by_status 按进度记录数聚合（多成员可大于 total_books），
// 条形图分母应使用状态总和而非 total_books，避免宽度超过 100%
const statusTotal = computed(() => {
  if (!stats.value) return 0
  return Object.values(stats.value.by_status).reduce((sum, n) => sum + n, 0)
})

async function load() {
  loading.value = true
  loadError.value = null
  try {
    stats.value = await api.get<StatsOut>('/stats')
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1 class="page-title">藏书统计</h1>

    <!-- 骨架屏（修复 P2） -->
    <div v-if="loading" class="skeleton-stat-grid" aria-hidden="true">
      <div v-for="n in 5" :key="n" class="skeleton-stat-card">
        <div class="skeleton-stat-value"></div>
        <div class="skeleton-stat-label"></div>
      </div>
    </div>

    <!-- BUG-125：加载失败时展示错误状态 + 重试 -->
    <div v-else-if="loadError" class="error-state">
      <p class="error-state-msg">{{ loadError }}</p>
      <button class="btn" @click="load">重试</button>
    </div>

    <div v-else-if="stats && stats.total_books === 0" class="empty">
      <div>暂无统计数据</div>
      <div class="empty-hint">入库书籍后即可查看统计</div>
    </div>

    <div v-else-if="stats">
      <!-- 数字卡片（D6：语义色） -->
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-value">{{ stats.total_books }}</div>
          <div class="stat-label">总藏书</div>
        </div>
        <div class="stat-card" style="--stat-color: var(--info);">
          <div class="stat-value">{{ stats.by_status.reading || 0 }}</div>
          <div class="stat-label">在读</div>
        </div>
        <div class="stat-card" style="--stat-color: var(--success);">
          <div class="stat-value">{{ stats.by_status.finished || 0 }}</div>
          <div class="stat-label">已读完</div>
        </div>
        <div class="stat-card" style="--stat-color: var(--warning);">
          <div class="stat-value">¥{{ stats.total_spent.toFixed(2) }}</div>
          <div class="stat-label">花费总额 ({{ stats.purchase_count }} 笔)</div>
        </div>
        <div class="stat-card">
          <div class="stat-value">{{ stats.reading_logs_pages_total }}</div>
          <div class="stat-label">累计阅读页数</div>
        </div>
      </div>

      <!-- 状态分布 -->
      <h3 class="section-title">阅读状态分布</h3>
      <div class="section-block">
        <div v-for="s in READING_STATUSES.filter(s => s.value)" :key="s.value" class="category-bar">
          <span class="category-bar-label">{{ s.label }}</span>
          <div
            class="category-bar-track"
            role="img"
            :aria-label="`占比 ${((stats.by_status[s.value] || 0) / Math.max(statusTotal, 1) * 100).toFixed(0)}%`"
          >
            <div
              class="category-bar-fill"
              :style="{ transform: `scaleX(${(stats.by_status[s.value] || 0) / Math.max(statusTotal, 1)})` }"
            ></div>
          </div>
          <span class="category-bar-count">{{ stats.by_status[s.value] || 0 }}</span>
        </div>
      </div>

      <!-- 分类分布 -->
      <h3 class="section-title">分类分布</h3>
      <div class="section-block">
        <div v-for="c in stats.by_category" :key="c.category" class="category-bar">
          <span class="category-bar-label">{{ c.category }}</span>
          <div
            class="category-bar-track"
            role="img"
            :aria-label="`占比 ${maxCategoryCount > 0 ? (c.count / maxCategoryCount * 100).toFixed(0) : 0}%`"
          >
            <div
              class="category-bar-fill"
              :style="{ transform: `scaleX(${maxCategoryCount > 0 ? c.count / maxCategoryCount : 0})` }"
            ></div>
          </div>
          <span class="category-bar-count">{{ c.count }}</span>
        </div>
        <p v-if="stats.by_category.length === 0" class="muted-text">暂无分类数据</p>
      </div>

      <!-- 成员统计 -->
      <h3 class="section-title">成员阅读统计</h3>
      <div v-if="stats.members.length === 0" class="empty">
        <div>暂无成员数据</div>
        <div class="empty-hint">添加家庭成员后即可查看阅读统计</div>
      </div>
      <div v-else class="table-wrap">
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

      <!-- 年度趋势 -->
      <h3 class="section-title">年度趋势</h3>
      <p v-if="stats.by_year.length === 0" class="empty-hint">暂无年度数据</p>
      <div v-else class="yearly-table">
        <table class="data-table">
          <thead>
            <tr><th>年份</th><th>入库</th><th>花费</th><th>阅读页数</th></tr>
          </thead>
          <tbody>
            <tr v-for="y in stats.by_year" :key="y.year">
              <td style="font-weight: 600;">{{ y.year }}</td>
              <td>{{ y.books_added }} 本</td>
              <td>¥{{ y.spent.toFixed(2) }}</td>
              <td>{{ y.pages_read }} 页</td>
            </tr>
          </tbody>
        </table>
        <!-- 花费趋势条形图 -->
        <h4 class="subsection-title">花费趋势</h4>
        <div v-for="y in stats.by_year" :key="`spent-${y.year}`" class="category-bar">
          <span class="category-bar-label">{{ y.year }}</span>
          <div
            class="category-bar-track"
            role="img"
            :aria-label="`占比 ${maxYearlySpent > 0 ? (y.spent / maxYearlySpent * 100).toFixed(0) : 0}%`"
          >
            <div
              class="category-bar-fill"
              :style="{ transform: `scaleX(${maxYearlySpent > 0 ? y.spent / maxYearlySpent : 0})` }"
            ></div>
          </div>
          <span class="category-bar-count category-bar-spent">¥{{ y.spent.toFixed(0) }}</span>
        </div>
      </div>

      <!-- 概览图入口 -->
      <div class="overview-link">
        <RouterLink to="/overview" class="btn">📷 生成我家书架概览图</RouterLink>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page-title {
  margin-bottom: 20px;
  font-family: var(--font-serif);
}

/* 年度趋势区块 */
.yearly-table {
  margin-bottom: 24px;
}

.subsection-title {
  margin: 20px 0 12px;
  font-size: 14px;
  font-weight: 600;
}

/* 概览图入口 */
.overview-link {
  margin-top: 32px;
  text-align: center;
}

/* 花费趋势金额列略宽 */
.category-bar-spent {
  width: 60px;
}

/* BUG-125：加载失败状态 */
.error-state {
  text-align: center;
  padding: 48px 24px;
}
.error-state-msg {
  color: var(--error-text);
  margin-bottom: 16px;
}
</style>
