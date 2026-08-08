<script setup lang="ts">
import { onMounted } from 'vue'
import { RouterLink, RouterView } from 'vue-router'
import { useMembersStore } from '@/stores/members'
import { lastError } from '@/stores/api'

const members = useMembersStore()
onMounted(() => {
  if (members.members.length === 0) {
    members.load().catch(() => {})
  }
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <RouterLink to="/" class="logo">📚 家庭书架</RouterLink>
      <nav class="nav">
        <RouterLink to="/">书架</RouterLink>
        <RouterLink to="/stats">统计</RouterLink>
      </nav>
      <div class="member-selector">
        <label>成员</label>
        <select
          :value="members.selectedId || ''"
          @change="members.select(parseInt(($event.target as HTMLSelectElement).value, 10))"
        >
          <option v-for="m in members.members" :key="m.id" :value="m.id">
            {{ m.name }}
          </option>
        </select>
      </div>
    </header>

    <div v-if="lastError" class="error-banner" @click="lastError = null">
      ⚠ {{ lastError }}
    </div>

    <main class="content">
      <RouterView />
    </main>
  </div>
</template>
