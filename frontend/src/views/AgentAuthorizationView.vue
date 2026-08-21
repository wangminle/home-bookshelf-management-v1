<script setup lang="ts">
/**
 * WBS-7：Agent 授权管理页面（Owner 专用）
 *
 * 功能：
 * - 注册新 Agent 客户端
 * - 创建授权（选择成员 + Scope + 有效期）
 * - 签发 Token（只显示一次）
 * - 查看授权列表和状态
 * - 撤销授权/令牌
 */
import { ref, onMounted } from 'vue'
import ScopeSelector from '@/components/ScopeSelector.vue'
import { invalidateSession } from '@/stores/session'

// ── 类型 ──
interface AgentClient {
  id: number
  public_id: string
  display_name: string
  client_type: string | null
  created_at: string
  revoked_at: string | null
}
interface AgentGrant {
  id: number
  agent_client_id: number
  member_id: number
  scopes: string[]
  status: string
  expires_at: string
  approved_at: string
  revoked_at: string | null
}
interface Member {
  id: number
  name: string
  role: string
}

// ── 状态 ──
const authStatus = ref<{ authenticated: boolean; member_id?: number; member_name?: string } | null>(null)
const passwordInitialized = ref(false)
const initPassword = ref('')
const initPasswordConfirm = ref('')
const initError = ref('')

const clients = ref<AgentClient[]>([])
const grants = ref<AgentGrant[]>([])
const members = ref<Member[]>([])

// 新建 Agent
const newClientName = ref('')
const newClientType = ref('')
const showCreateClient = ref(false)

// 新建授权
const showCreateGrant = ref(false)
const grantClientId = ref<number | null>(null)
const grantMemberId = ref<number | null>(null)
const grantScopes = ref<string[]>([])
const grantExpiryDays = ref(30)
// CHK-073：显式数据范围标记（勾选=专用试点 Grant，MCP 可用）
const grantDataScope = ref(false)
const grantError = ref('')

// 新签发 Token
const issuedToken = ref<string | null>(null)
const issuedTokenGrantId = ref<number | null>(null)

// ── API 调用 ──
const BASE = import.meta.env.BASE_URL

async function apiCall(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }))
    throw new Error(body.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

async function checkAuthStatus() {
  try {
    const [session, status] = await Promise.all([
      apiCall('auth/session'),
      apiCall('auth/status'),
    ])
    authStatus.value = session
    passwordInitialized.value = status.password_initialized
    if (session.authenticated) {
      await loadData()
    }
  } catch {
    authStatus.value = { authenticated: false }
  }
}

async function doInitPassword() {
  initError.value = ''
  if (initPassword.value !== initPasswordConfirm.value) {
    initError.value = '两次输入的密码不一致'
    return
  }
  if (initPassword.value.length < 8) {
    initError.value = '密码至少 8 位'
    return
  }
  try {
    await apiCall('auth/init-password', {
      method: 'POST',
      body: JSON.stringify({ password: initPassword.value, confirm: initPasswordConfirm.value }),
    })
    initPassword.value = ''
    initPasswordConfirm.value = ''
    await checkAuthStatus()
  } catch (e: any) {
    initError.value = e.message
  }
}

async function doLogout() {
  try {
    await apiCall('auth/logout', { method: 'POST' })
  } catch {}
  authStatus.value = { authenticated: false }
  // CHK-071：登出后失效会话缓存，守卫下次重新探测
  invalidateSession()
}

async function loadData() {
  try {
    const [c, g, m] = await Promise.all([
      apiCall('agent-access/clients'),
      apiCall('agent-access/grants'),
      apiCall('api/v1/members'),
    ])
    clients.value = c
    grants.value = g
    members.value = m.data?.items || []
  } catch (e) {
    console.error('Failed to load data:', e)
  }
}

async function createClient() {
  if (!newClientName.value.trim()) return
  try {
    await apiCall('agent-access/clients', {
      method: 'POST',
      body: JSON.stringify({
        display_name: newClientName.value,
        client_type: newClientType.value || null,
      }),
    })
    newClientName.value = ''
    newClientType.value = ''
    showCreateClient.value = false
    await loadData()
  } catch (e: any) {
    alert(e.message)
  }
}

async function createGrant() {
  grantError.value = ''
  if (!grantClientId.value || !grantMemberId.value || grantScopes.value.length === 0) {
    grantError.value = '请填写所有必填项'
    return
  }
  // 高风险 Scope 二次确认
  const highRisk = grantScopes.value.filter(s => s.includes('delete') || s.includes('household'))
  if (highRisk.length > 0) {
    if (!confirm(`你正在授予高风险权限：${highRisk.join(', ')}。确认继续？`)) {
      return
    }
  }
  try {
    // CHK-073/BUG-197：显式声明数据范围=专用试点 Grant（MCP 等真实数据门控
    // 只接受 household_shared 标记；不勾选=历史语义 Grant）
    const payload: Record<string, unknown> = {
      agent_client_id: grantClientId.value,
      member_id: grantMemberId.value,
      scopes: grantScopes.value,
      expires_in_days: grantExpiryDays.value,
    }
    if (grantDataScope.value) {
      payload.data_scope = 'household_shared'
    }
    await apiCall('agent-access/grants', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    showCreateGrant.value = false
    grantScopes.value = []
    grantClientId.value = null
    grantMemberId.value = null
    grantExpiryDays.value = 30
    grantDataScope.value = false
    await loadData()
  } catch (e: any) {
    grantError.value = e.message
  }
}

async function issueToken(grantId: number) {
  try {
    const result = await apiCall('agent-access/tokens', {
      method: 'POST',
      body: JSON.stringify({ grant_id: grantId }),
    })
    issuedToken.value = result.token
    issuedTokenGrantId.value = grantId
  } catch (e: any) {
    alert(e.message)
  }
}

async function revokeGrant(grantId: number) {
  if (!confirm('确认撤销此授权？关联的所有令牌将立即失效。')) return
  try {
    await apiCall(`agent-access/grants/${grantId}`, { method: 'DELETE' })
    await loadData()
  } catch (e: any) {
    alert(e.message)
  }
}

async function revokeClient(clientId: number) {
  if (!confirm('确认撤销此 Agent 客户端？所有关联授权和令牌将立即失效。')) return
  try {
    await apiCall(`agent-access/clients/${clientId}`, { method: 'DELETE' })
    await loadData()
  } catch (e: any) {
    alert(e.message)
  }
}

function copyToken() {
  if (issuedToken.value) {
    navigator.clipboard.writeText(issuedToken.value)
  }
}

function clientName(id: number): string {
  return clients.value.find(c => c.id === id)?.display_name || `#${id}`
}

function memberName(id: number): string {
  return members.value.find(m => m.id === id)?.name || `#${id}`
}

function formatDate(s: string): string {
  return new Date(s).toLocaleString('zh-CN')
}

function isExpired(dateStr: string): boolean {
  return new Date(dateStr) < new Date()
}

onMounted(() => {
  checkAuthStatus()
})
</script>

<template>
  <div class="agent-auth-view">
    <!-- 未认证：登录/初始化 -->
    <div v-if="!authStatus?.authenticated" class="auth-gate">
      <div v-if="!passwordInitialized" class="init-password">
        <h2>🔑 首次设置 Owner 密码</h2>
        <p>系统尚未设置 Owner 密码，请先初始化以管理 Agent 授权。</p>
        <input
          v-model="initPassword"
          type="password"
          placeholder="新密码（≥8 位）"
          aria-label="新密码"
        />
        <input
          v-model="initPasswordConfirm"
          type="password"
          placeholder="确认密码"
          aria-label="确认密码"
        />
        <p v-if="initError" class="error">{{ initError }}</p>
        <button @click="doInitPassword">设置密码</button>
      </div>
      <div v-else class="login">
        <h2>🔐 需要登录</h2>
        <!-- 权限阶段 2（基线 §10.2）：统一登录页，授权页不再内嵌登录表单 -->
        <RouterLink to="/login" class="btn-primary">前往登录</RouterLink>
      </div>
    </div>

    <!-- 已认证：授权管理 -->
    <div v-else class="auth-content">
      <header class="page-header">
        <h1>🤖 Agent 授权管理</h1>
        <div class="header-actions">
          <span class="user-badge">👤 {{ authStatus?.member_name }}</span>
          <button @click="doLogout" class="btn-logout">退出</button>
        </div>
      </header>

      <!-- Agent 客户端列表 -->
      <section class="card">
        <div class="card-header">
          <h2>Agent 客户端</h2>
          <button @click="showCreateClient = !showCreateClient" class="btn-primary">
            {{ showCreateClient ? '取消' : '+ 注册新 Agent' }}
          </button>
        </div>
        <div v-if="showCreateClient" class="create-form">
          <input v-model="newClientName" placeholder="Agent 名称（如 Codex、Hermes）" />
          <input v-model="newClientType" placeholder="类型（可选，如 codex/openclaw/custom）" />
          <button @click="createClient" class="btn-primary">创建</button>
        </div>
        <table v-if="clients.length > 0" class="data-table">
          <thead>
            <tr>
              <th>ID</th><th>名称</th><th>类型</th><th>创建时间</th><th>状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in clients" :key="c.id" :class="{ revoked: c.revoked_at }">
              <td>{{ c.id }}</td>
              <td>{{ c.display_name }}</td>
              <td>{{ c.client_type || '-' }}</td>
              <td>{{ formatDate(c.created_at) }}</td>
              <td>{{ c.revoked_at ? '已撤销' : '活跃' }}</td>
              <td>
                <button v-if="!c.revoked_at" @click="revokeClient(c.id)" class="btn-danger">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty">暂无 Agent 客户端——请先点击上方「+ 注册新 Agent」注册</p>
      </section>

      <!-- 授权列表 -->
      <section class="card">
        <div class="card-header">
          <h2>授权列表</h2>
          <button
            @click="showCreateGrant = !showCreateGrant"
            class="btn-primary"
            :disabled="clients.length === 0"
            :title="clients.length === 0 ? '请先注册 Agent 客户端，再创建授权' : ''"
          >
            {{ showCreateGrant ? '取消' : '+ 创建授权' }}
          </button>
          <span v-if="clients.length === 0" class="hint">需先在上方注册 Agent 客户端</span>
        </div>
        <div v-if="showCreateGrant" class="create-form grant-form">
          <label>Agent 客户端
            <select v-model="grantClientId">
              <option :value="null" disabled>选择 Agent</option>
              <option v-for="c in clients.filter(c => !c.revoked_at)" :key="c.id" :value="c.id">
                {{ c.display_name }}
              </option>
            </select>
          </label>
          <label>绑定成员
            <select v-model="grantMemberId">
              <option :value="null" disabled>选择成员</option>
              <option v-for="m in members" :key="m.id" :value="m.id">{{ m.name }} ({{ m.role }})</option>
            </select>
          </label>
          <label>有效期（天）
            <input v-model.number="grantExpiryDays" type="number" min="1" max="365" />
          </label>
          <div class="scope-section">
            <p class="form-label">权限范围</p>
            <ScopeSelector v-model="grantScopes" />
          </div>
          <label class="checkbox-row">
            <input v-model="grantDataScope" type="checkbox" />
            <span>专用试点 Grant（数据范围：家庭共享书目 household_shared）——MCP 只读试点必须勾选</span>
          </label>
          <p v-if="grantError" class="error">{{ grantError }}</p>
          <button @click="createGrant" class="btn-primary">创建授权</button>
        </div>
        <table v-if="grants.length > 0" class="data-table">
          <thead>
            <tr>
              <th>ID</th><th>Agent</th><th>成员</th><th>Scope</th><th>状态</th><th>到期</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="g in grants" :key="g.id" :class="{ revoked: g.status !== 'active' }">
              <td>{{ g.id }}</td>
              <td>{{ clientName(g.agent_client_id) }}</td>
              <td>{{ memberName(g.member_id) }}</td>
              <td>
                <span v-for="s in g.scopes" :key="s" class="scope-tag">{{ s }}</span>
              </td>
              <td>
                <span :class="['status-badge', g.status]">{{ g.status }}</span>
                <span v-if="g.status === 'active' && isExpired(g.expires_at)" class="status-badge expired">已过期</span>
              </td>
              <td>{{ formatDate(g.expires_at) }}</td>
              <td>
                <button v-if="g.status === 'active' && !isExpired(g.expires_at)" @click="issueToken(g.id)" class="btn-secondary">签发令牌</button>
                <button v-if="g.status === 'active'" @click="revokeGrant(g.id)" class="btn-danger">撤销</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p v-else class="empty">暂无授权</p>
      </section>

      <!-- Token 显示弹窗 -->
      <div v-if="issuedToken" class="token-modal" @click.self="issuedToken = null">
        <div class="token-modal-content">
          <h3>🔑 新令牌（只显示一次）</h3>
          <p class="warning">⚠️ 此令牌只显示一次，请立即复制并配置到 Agent 环境变量。关闭后将无法再次查看。</p>
          <code class="token-display">{{ issuedToken }}</code>
          <div class="token-actions">
            <button @click="copyToken" class="btn-primary">📋 复制</button>
            <button @click="issuedToken = null" class="btn-secondary">关闭</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.agent-auth-view {
  max-width: 960px;
  margin: 0 auto;
  padding: 1rem;
}
.auth-gate {
  max-width: 400px;
  margin: 4rem auto;
  padding: 2rem;
  text-align: center;
}
.auth-gate h2 {
  margin-bottom: 1rem;
}
.auth-gate input {
  display: block;
  width: 100%;
  padding: 0.75rem;
  margin: 0.5rem 0;
  border: 1px solid #ccc;
  border-radius: 4px;
}
.auth-gate button {
  padding: 0.75rem 2rem;
  background: var(--primary, #4a90d9);
  color: white;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  margin-top: 0.5rem;
}
.error {
  color: #c62828;
  font-size: 0.875rem;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1.5rem;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}
.user-badge {
  font-size: 0.875rem;
}
.card {
  background: var(--bg-card, white);
  border: 1px solid var(--border-color, #e0e0e0);
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}
.create-form {
  padding: 1rem;
  background: var(--bg-form, #f9f9f9);
  border-radius: 6px;
  margin-bottom: 1rem;
}
.create-form input,
.create-form select {
  display: block;
  width: 100%;
  padding: 0.5rem;
  margin: 0.5rem 0;
  border: 1px solid #ccc;
  border-radius: 4px;
}
.grant-form label {
  display: block;
  margin-bottom: 0.5rem;
  font-weight: 500;
}
.grant-form select,
.grant-form input {
  margin-top: 0.25rem;
}
.scope-section {
  margin: 1rem 0;
}
.form-label {
  font-weight: 600;
  margin-bottom: 0.5rem;
}
.data-table {
  width: 100%;
  border-collapse: collapse;
}
.data-table th,
.data-table td {
  padding: 0.5rem;
  text-align: left;
  border-bottom: 1px solid var(--border-color, #e0e0e0);
}
.data-table th {
  font-weight: 600;
  background: var(--bg-header, #f5f5f5);
}
.data-table tr.revoked {
  opacity: 0.5;
}
.scope-tag {
  display: inline-block;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  margin: 0.1rem;
  background: #e3f2fd;
  border-radius: 3px;
}
.status-badge {
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
}
.status-badge.active {
  background: #e8f5e9;
  color: #2e7d32;
}
.status-badge.revoked {
  background: #ffebee;
  color: #c62828;
}
.status-badge.expired {
  background: #fff3e0;
  color: #e65100;
}
.empty {
  color: var(--text-secondary, #666);
  text-align: center;
  padding: 2rem;
}
.hint {
  color: var(--text-secondary, #666);
  font-size: 0.85rem;
  margin-left: 0.5rem;
  align-self: center;
}
.btn-primary {
  background: var(--primary, #4a90d9);
  color: white;
  border: none;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
}
.btn-secondary {
  background: #f0f0f0;
  border: 1px solid #ccc;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  cursor: pointer;
  margin-left: 0.5rem;
}
.btn-danger {
  background: #e53935;
  color: white;
  border: none;
  padding: 0.25rem 0.75rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
}
.btn-logout {
  background: #f0f0f0;
  border: 1px solid #ccc;
  padding: 0.3rem 0.8rem;
  border-radius: 4px;
  cursor: pointer;
  font-size: 0.8rem;
}
.token-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.token-modal-content {
  background: white;
  padding: 2rem;
  border-radius: 8px;
  max-width: 600px;
  width: 90%;
}
.token-modal-content h3 {
  margin-bottom: 1rem;
}
.warning {
  color: #e65100;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}
.token-display {
  display: block;
  padding: 1rem;
  background: #f5f5f5;
  border-radius: 4px;
  word-break: break-all;
  font-family: monospace;
  font-size: 0.875rem;
  margin-bottom: 1rem;
}
.token-actions {
  display: flex;
  gap: 0.5rem;
}
</style>
