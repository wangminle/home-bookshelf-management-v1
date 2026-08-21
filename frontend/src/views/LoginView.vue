<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { probeSession } from '@/stores/session'

/**
 * 统一登录页（权限阶段 2，基线 §10.2）：Owner/Member 共用同一入口，
 * 不再内嵌在 Agent 授权页。用户名可省略——系统只有一条凭据时后端自动回退。
 */
const router = useRouter()
const username = ref('')
const password = ref('')
const error = ref('')
const submitting = ref(false)

async function doLogin() {
  error.value = ''
  if (!password.value) {
    error.value = '请输入密码'
    return
  }
  submitting.value = true
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}auth/login`, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        username: username.value || undefined,
        password: password.value,
      }),
    })
    const body = await res.json().catch(() => ({}))
    if (!res.ok) {
      error.value = body.detail || `登录失败 (${res.status})`
      return
    }
    await probeSession(true)
    router.push('/')
  } catch {
    error.value = '无法连接到服务器'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="login-view">
    <form class="login-card" @submit.prevent="doLogin">
      <h1>登录家庭书架</h1>
      <p class="hint">家庭成员与 Owner 使用同一入口；只有一个账号时用户名可留空。</p>
      <label for="login-username">用户名</label>
      <input
        id="login-username"
        v-model="username"
        type="text"
        autocomplete="username"
        placeholder="用户名（单账号可留空）"
      />
      <label for="login-password">密码</label>
      <input
        id="login-password"
        v-model="password"
        type="password"
        autocomplete="current-password"
        placeholder="密码"
      />
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button type="submit" :disabled="submitting">
        {{ submitting ? '登录中…' : '登录' }}
      </button>
      <RouterLink to="/shared" class="to-shared">先逛逛共享书架 →</RouterLink>
    </form>
  </section>
</template>

<style scoped>
.login-view { display: flex; justify-content: center; padding: var(--space-5, 32px) var(--space-3, 12px); }
.login-card { background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 14px; padding: 32px; width: min(380px, 100%); display: flex; flex-direction: column; gap: 8px; }
.login-card h1 { margin: 0 0 4px; font-size: 1.4rem; }
.hint { color: var(--text-muted, #5a6878); font-size: 0.88rem; margin: 0 0 12px; }
label { font-size: 0.9rem; color: var(--text-muted, #5a6878); }
input { padding: 10px 12px; border-radius: 8px; border: 1px solid var(--border, #d7dee8); background: var(--bg, #fff); color: inherit; }
button { margin-top: 12px; padding: 10px; border-radius: 8px; border: none; background: var(--primary, #2c7a7b); color: #fff; cursor: pointer; font-size: 1rem; }
button:disabled { opacity: 0.6; cursor: default; }
.error { color: #b3261e; font-size: 0.9rem; margin: 0; }
.to-shared { margin-top: 16px; text-align: center; color: var(--primary, #2c7a7b); text-decoration: none; font-size: 0.9rem; }
</style>
