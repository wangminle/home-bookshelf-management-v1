<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useMembersStore } from '@/stores/members'
import { lastError, backendOffline } from '@/stores/api'

const members = useMembersStore()
// 成员列表在 main.ts 启动时已加载，此处不再重复请求（修复 P2：避免重复加载竞态）
onMounted(() => {
  if (members.members.length === 0) {
    members.load().catch(() => {})
  }
})

// 修复 BUG：select 被清空时 parseInt('') 返回 NaN，会写入 localStorage 脏数据
function onMemberChange(e: Event) {
  const raw = (e.target as HTMLSelectElement).value
  const id = parseInt(raw, 10)
  if (!Number.isNaN(id)) {
    members.select(id)
  }
}
</script>

<template>
  <div class="app">
    <header class="topbar">
      <RouterLink to="/" class="logo" aria-label="家庭书架首页">📚 家庭书架</RouterLink>
      <nav class="nav" aria-label="主导航">
        <RouterLink to="/">书架</RouterLink>
        <RouterLink to="/stats">统计</RouterLink>
        <RouterLink to="/overview">概览图</RouterLink>
      </nav>
      <div class="member-selector">
        <label for="member-select">成员</label>
        <select
          id="member-select"
          :value="members.selectedId || ''"
          aria-label="选择家庭成员"
          @change="onMemberChange"
        >
          <option v-for="m in members.members" :key="m.id" :value="m.id">
            {{ m.name }}
          </option>
        </select>
      </div>
    </header>

    <!-- 修复 P1：错误横幅添加 role=alert + aria-live，屏幕阅读器可播报 -->
    <div
      v-if="lastError"
      class="error-banner"
      role="alert"
      aria-live="assertive"
      @click="lastError = null"
      @keyup.enter="lastError = null"
    >
      <span aria-hidden="true">⚠</span>
      <span>{{ lastError }}</span>
    </div>

    <!-- 修复 P2：后端离线连接提示 -->
    <div
      v-if="backendOffline"
      class="error-banner"
      role="status"
      aria-live="polite"
    >
      <span aria-hidden="true">🔌</span>
      <span>无法连接到服务器，请确认后端服务已启动后刷新页面</span>
    </div>

    <main class="content">
      <RouterView v-slot="{ Component }">
        <Transition name="fade" mode="out-in">
          <component :is="Component" />
        </Transition>
      </RouterView>
    </main>
  </div>
</template>
