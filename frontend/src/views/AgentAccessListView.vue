<script setup lang="ts">
/** WBS-7：Agent 授权列表总览页。

展示所有 Agent 客户端、其授权状态、Token 使用情况和到期信息。
与 AgentAuthorizationView（管理页）互补：本页只读总览，管理操作跳转至授权管理页。
*/
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
// CHK-039 P2: auth/* 和 agent-access/* 路由注册在根级，不在 /api/v1 下。
// 使用 BASE_URL（部署基址）而非 VITE_API_BASE（业务 API 前缀）。
const BASE = import.meta.env.BASE_URL

// ── 状态 ──
const loading = ref(false)
const error = ref('')
const authenticated = ref(false)
const passwordInitialized = ref(false)

interface AgentClient {
  id: number
  public_id: string
  display_name: string
  client_type: string
  last_seen_at: string | null
  revoked_at: string | null
  created_at: string
}

interface AgentGrant {
  id: number
  agent_client_id: number
  member_id: number
  scopes: string[]
  status: string
  expires_at: string | null
  approved_at: string | null
  revoked_at: string | null
  created_at: string
}

interface TokenInfo {
  id: number
  grant_id: number
  token_prefix: string
  issued_at: string
  expires_at: string
  last_used_at: string | null
  revoked_at: string | null
}

const clients = ref<AgentClient[]>([])
const grants = ref<AgentGrant[]>([])
const tokens = ref<TokenInfo[]>([])

// ── 计算属性 ──

/** 按 client 聚合：每个 client 的授权数、活跃 token 数、最后使用时间 */
interface ClientOverview {
  client: AgentClient
  grantCount: number
  activeGrantCount: number
  tokenCount: number
  activeTokenCount: number
  lastUsedAt: string | null
  isRevoked: boolean
}

const clientOverviews = computed<ClientOverview[]>(() => {
  return clients.value.map((c) => {
    const cGrants = grants.value.filter((g) => g.agent_client_id === c.id)
    const cTokens = tokens.value.filter((t) =>
      cGrants.some((g) => g.id === t.grant_id),
    )
    const lastUsedTimes = cTokens
      .map((t) => t.last_used_at)
      .filter((v): v is string => v !== null)
    return {
      client: c,
      grantCount: cGrants.length,
      activeGrantCount: cGrants.filter((g) => g.status === 'active').length,
      tokenCount: cTokens.length,
      activeTokenCount: cTokens.filter(
        (t) => !t.revoked_at && !isExpired(t.expires_at),
      ).length,
      lastUsedAt: lastUsedTimes.length
        ? lastUsedTimes.sort().reverse()[0]
        : null,
      isRevoked: c.revoked_at !== null,
    }
  })
})

const totalActiveGrants = computed(() =>
  grants.value.filter((g) => g.status === 'active').length,
)
const totalActiveTokens = computed(() =>
  tokens.value.filter((t) => !t.revoked_at && !isExpired(t.expires_at)).length,
)
const expiringSoon = computed(() =>
  grants.value.filter(
    (g) =>
      g.status === 'active' &&
      g.expires_at &&
      !isExpired(g.expires_at) &&
      daysUntil(g.expires_at) <= 7,
  ).length,
)

// ── 工具函数 ──

function isExpired(dateStr: string | null): boolean {
  if (!dateStr) return false
  return new Date(dateStr) < new Date()
}

function daysUntil(dateStr: string): number {
  const diff = new Date(dateStr).getTime() - Date.now()
  return Math.ceil(diff / (1000 * 60 * 60 * 24))
}

function formatDate(s: string | null): string {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function scopeColor(scope: string): string {
  if (scope.includes('delete')) return 'high'
  if (scope.includes('write') || scope.includes('household')) return 'medium'
  return 'low'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    active: '活跃',
    revoked: '已撤销',
    expired: '已过期',
  }
  return map[status] || status
}

// ── API ──

async function apiCall(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || res.statusText)
  }
  return res.json()
}

async function checkAuthStatus() {
  try {
    const status = await apiCall('auth/status')
    passwordInitialized.value = status.password_initialized
    if (!status.password_initialized) {
      error.value = 'Owner 密码尚未初始化，请先在授权管理页设置密码'
      return
    }
    const session = await apiCall('auth/session')
    authenticated.value = session.authenticated === true
    if (!authenticated.value) {
      error.value = '请先登录'
    }
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function loadData() {
  loading.value = true
  error.value = ''
  try {
    const [c, g] = await Promise.all([
      apiCall('agent-access/clients'),
      apiCall('agent-access/grants'),
    ])
    clients.value = Array.isArray(c) ? c : []
    grants.value = Array.isArray(g) ? g : []

    // 并行加载每个 grant 的 tokens
    const tokenPromises = grants.value.map((grant) =>
      apiCall(`agent-access/tokens/${grant.id}`).catch(() => []),
    )
    const tokenResults = await Promise.all(tokenPromises)
    tokens.value = tokenResults.flat()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

function goToManagement() {
  router.push('/agent-authorization')
}

function goToConnect() {
  router.push('/agent')
}

onMounted(async () => {
  await checkAuthStatus()
  if (authenticated.value) {
    await loadData()
  }
})
</script>

<template>
  <div class="agent-access-list">
    <h2>Agent 授权总览</h2>

    <!-- 未登录 -->
    <div v-if="!authenticated && passwordInitialized" class="login-gate">
      <p>请登录后查看授权列表</p>
      <RouterLink to="/login">前往登录</RouterLink>
    </div>

    <!-- 未初始化 -->
    <div v-if="!passwordInitialized" class="init-gate">
      <p>{{ error }}</p>
      <button @click="goToManagement">前往授权管理页</button>
    </div>

    <p v-if="error && authenticated" class="error">{{ error }}</p>

    <!-- 已登录：总览 -->
    <div v-if="authenticated && !loading" class="overview">
      <!-- 统计卡片 -->
      <div class="stats-row">
        <div class="stat-card">
          <span class="stat-value">{{ clients.length }}</span>
          <span class="stat-label">Agent 客户端</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ totalActiveGrants }}</span>
          <span class="stat-label">活跃授权</span>
        </div>
        <div class="stat-card">
          <span class="stat-value">{{ totalActiveTokens }}</span>
          <span class="stat-label">活跃 Token</span>
        </div>
        <div class="stat-card" :class="{ warning: expiringSoon > 0 }">
          <span class="stat-value">{{ expiringSoon }}</span>
          <span class="stat-label">7 天内到期</span>
        </div>
      </div>

      <!-- Agent 列表 -->
      <div v-if="clientOverviews.length === 0" class="empty">
        <p>尚无 Agent 客户端</p>
        <button @click="goToManagement">创建第一个 Agent</button>
      </div>

      <table v-else class="agent-table">
        <thead>
          <tr>
            <th>名称</th>
            <th>类型</th>
            <th>授权数</th>
            <th>活跃 Token</th>
            <th>最后使用</th>
            <th>状态</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="co in clientOverviews"
            :key="co.client.id"
            :class="{ revoked: co.isRevoked }"
          >
            <td>{{ co.client.display_name }}</td>
            <td>{{ co.client.client_type || '—' }}</td>
            <td>
              {{ co.activeGrantCount }} / {{ co.grantCount }}
            </td>
            <td>{{ co.activeTokenCount }} / {{ co.tokenCount }}</td>
            <td>{{ formatDate(co.lastUsedAt) }}</td>
            <td>
              <span v-if="co.isRevoked" class="badge badge-revoked">已撤销</span>
              <span v-else class="badge badge-active">正常</span>
            </td>
            <td>{{ formatDate(co.client.created_at) }}</td>
          </tr>
        </tbody>
      </table>

      <!-- 授权详情列表 -->
      <h3>授权详情</h3>
      <div v-if="grants.length === 0" class="empty-small">
        <p>尚无授权记录</p>
      </div>
      <div v-else class="grants-list">
        <div v-for="g in grants" :key="g.id" class="grant-card" :class="{ revoked: g.revoked_at }">
          <div class="grant-header">
            <span class="grant-name">
              {{ clients.find((c) => c.id === g.agent_client_id)?.display_name || `Client #${g.agent_client_id}` }}
            </span>
            <span class="badge" :class="`badge-${g.status}`">{{ statusLabel(g.status) }}</span>
          </div>
          <div class="grant-scopes">
            <span
              v-for="s in g.scopes"
              :key="s"
              class="scope-tag"
              :class="`scope-${scopeColor(s)}`"
            >{{ s }}</span>
          </div>
          <div class="grant-meta">
            <span>到期: {{ formatDate(g.expires_at) }}</span>
            <span v-if="g.expires_at && !isExpired(g.expires_at) && daysUntil(g.expires_at) <= 7" class="warning-text">
              （{{ daysUntil(g.expires_at) }} 天后到期）
            </span>
            <span v-if="g.expires_at && isExpired(g.expires_at)" class="expired-text">（已过期）</span>
          </div>
        </div>
      </div>

      <!-- 操作按钮 -->
      <div class="actions">
        <button @click="goToManagement">管理授权</button>
        <button @click="goToConnect">Agent 连接信息</button>
        <button @click="loadData">刷新</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-access-list {
  max-width: 900px;
  margin: 0 auto;
  padding: 1rem;
}

h2 {
  margin-bottom: 1rem;
}

h3 {
  margin-top: 2rem;
  margin-bottom: 0.75rem;
}

.login-gate,
.init-gate {
  text-align: center;
  padding: 2rem;
}

.login-gate input {
  padding: 0.5rem;
  margin: 0 0.5rem;
  border: 1px solid #ccc;
  border-radius: 4px;
}

.error {
  color: #c0392b;
  padding: 0.5rem;
}

.stats-row {
  display: flex;
  gap: 1rem;
  margin-bottom: 1.5rem;
}

.stat-card {
  flex: 1;
  text-align: center;
  padding: 1rem;
  border: 1px solid #ddd;
  border-radius: 8px;
  background: #f9f9f9;
}

.stat-card.warning {
  border-color: #e67e22;
  background: #fef9f3;
}

.stat-value {
  display: block;
  font-size: 1.75rem;
  font-weight: bold;
}

.stat-label {
  display: block;
  font-size: 0.85rem;
  color: #666;
  margin-top: 0.25rem;
}

.empty,
.empty-small {
  text-align: center;
  padding: 1.5rem;
  color: #888;
}

.agent-table {
  width: 100%;
  border-collapse: collapse;
}

.agent-table th,
.agent-table td {
  padding: 0.6rem 0.5rem;
  text-align: left;
  border-bottom: 1px solid #eee;
}

.agent-table th {
  font-size: 0.85rem;
  color: #666;
}

.agent-table tr.revoked {
  opacity: 0.5;
}

.badge {
  display: inline-block;
  padding: 0.15rem 0.5rem;
  border-radius: 4px;
  font-size: 0.8rem;
}

.badge-active {
  background: #d4edda;
  color: #155724;
}

.badge-revoked {
  background: #f8d7da;
  color: #721c24;
}

.badge-expired {
  background: #fff3cd;
  color: #856404;
}

.grants-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.grant-card {
  padding: 0.75rem 1rem;
  border: 1px solid #ddd;
  border-radius: 8px;
}

.grant-card.revoked {
  opacity: 0.5;
}

.grant-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.5rem;
}

.grant-name {
  font-weight: 600;
}

.grant-scopes {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
  margin-bottom: 0.5rem;
}

.scope-tag {
  display: inline-block;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  font-size: 0.75rem;
  font-family: monospace;
}

.scope-low {
  background: #d4edda;
  color: #155724;
}

.scope-medium {
  background: #fff3cd;
  color: #856404;
}

.scope-high {
  background: #f8d7da;
  color: #721c24;
}

.grant-meta {
  font-size: 0.85rem;
  color: #666;
}

.warning-text {
  color: #e67e22;
}

.expired-text {
  color: #c0392b;
}

.actions {
  margin-top: 1.5rem;
  display: flex;
  gap: 0.75rem;
}

.actions button {
  padding: 0.5rem 1rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
}

.actions button:hover {
  background: #f0f0f0;
}
</style>
