<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useMembersStore } from '@/stores/members'
import { lastError, backendOffline } from '@/stores/api'
import { sessionAuthenticated, sessionRole, sessionMemberId, invalidateSession } from '@/stores/session'

const version = __APP_VERSION__

const members = useMembersStore()
// CHK-071：成员列表仅在会话确认后加载；匿名壳层（/shared）只展示
// 共享书架与登录入口，不触发受保护的 /members 请求。
onMounted(() => {
  if (sessionAuthenticated.value === true && members.members.length === 0) {
    members.load().then(fixMemberIdentity).catch(() => {})
  }
})
watch(sessionAuthenticated, (authed) => {
  if (authed === true && members.members.length === 0) {
    members.load().then(fixMemberIdentity).catch(() => {})
  }
})
// 权限阶段 2（基线 §3.3）：Member 固定显示本人身份，不出现成员切换器；
// 切换器仅 Owner 可见（Owner 代操作须显式选择归属人，后端仍校验代操作权限）。
function fixMemberIdentity() {
  if (sessionRole.value === 'member' && sessionMemberName.value != null) {
    // 按 ID 匹配（显示名可重复；重名时按名字匹配会选错成员）
    const self = members.members.find((m) => m.id === sessionMemberId.value)
    if (self) members.select(self.id)
  }
}

async function doLogout() {
  try {
    await fetch(`${import.meta.env.BASE_URL}auth/logout`, {
      method: 'POST',
      credentials: 'include',
    })
  } catch {}
  invalidateSession()
  window.location.assign(`${import.meta.env.BASE_URL}shared`)
}

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
      <span class="version-badge" aria-label="版本号">v{{ version }}</span>
      <nav class="nav" aria-label="主导航">
        <template v-if="sessionAuthenticated === true">
          <RouterLink to="/">书架</RouterLink>
          <RouterLink to="/stats">统计</RouterLink>
          <RouterLink to="/overview">概览图</RouterLink>
          <!-- 授权中心仅 Owner：Member 不显示管理入口（后端同样拒绝） -->
          <RouterLink v-if="sessionRole === 'owner'" to="/agent">Agent</RouterLink>
        </template>
        <RouterLink to="/shared">共享书架</RouterLink>
      </nav>
      <div class="member-selector" v-if="sessionAuthenticated === true">
        <label for="member-select">成员</label>
        <select
          id="member-select"
          :value="members.selectedId || ''"
          :disabled="members.members.length === 0"
          aria-label="选择家庭成员"
          @change="onMemberChange"
        >
          <option v-if="members.members.length === 0" value="" disabled>暂无成员</option>
          <option v-for="m in members.members" :key="m.id" :value="m.id">
            {{ m.name }}
          </option>
        </select>
      </div>
      <template v-else>
        <RouterLink to="/login" class="login-entry" aria-label="登录">登录</RouterLink>
      </template>
      <button
        v-if="sessionAuthenticated === true"
        class="logout-btn"
        type="button"
        aria-label="退出登录"
        @click="doLogout"
      >退出</button>
    </header>

    <!-- BUG-126：错误横幅用 <button> 确保键盘可达（div 不可聚焦，无法 Tab/Enter 关闭） -->
    <button
      v-if="lastError"
      class="error-banner"
      role="alert"
      aria-live="assertive"
      @click="lastError = null"
    >
      <span aria-hidden="true">⚠</span>
      <span>{{ lastError }}</span>
    </button>

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
