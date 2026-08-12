<script setup lang="ts">
/**
 * WBS-3：Agent 连接引导页
 *
 * 展示系统发现面信息，方便用户复制给 Agent：
 * - Manifest URL
 * - Bootstrap Markdown URL
 * - API Catalog URL
 * - OpenAPI URL
 * - Skills Index URL
 */
import { ref, onMounted } from 'vue'

const manifest = ref<any>(null)
const loading = ref(true)
const error = ref('')
const copiedField = ref('')

const BASE = import.meta.env.BASE_URL
const baseUrl = computed(() => {
  if (typeof window !== 'undefined') {
    return window.location.origin + BASE
  }
  return BASE
})

import { computed } from 'vue'

const connectUrls = computed(() => {
  const base = baseUrl.value
  return [
    { label: 'Manifest', url: `${base}agent/manifest.json`, desc: '系统机器清单（JSON）' },
    { label: 'Bootstrap', url: `${base}agent/bootstrap.md`, desc: 'Agent 初始化说明（Markdown）' },
    { label: 'API Catalog', url: `${base}.well-known/api-catalog`, desc: 'RFC 9727 API 目录' },
    { label: 'OpenAPI', url: `${base}agent/openapi.json`, desc: 'Agent API 规范' },
    { label: 'Skills Index', url: `${base}agent/skills/index.json`, desc: '可用 Skills 列表' },
    { label: 'LLMs.txt', url: `${base}llms.txt`, desc: '精简文档导航' },
  ]
})

async function loadManifest() {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`${BASE}agent/manifest.json`)
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    manifest.value = await res.json()
  } catch (e: any) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function copyUrl(url: string, label: string) {
  try {
    await navigator.clipboard.writeText(url)
    copiedField.value = label
    setTimeout(() => { copiedField.value = '' }, 2000)
  } catch {
    // Fallback
    const ta = document.createElement('textarea')
    ta.value = url
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    copiedField.value = label
    setTimeout(() => { copiedField.value = '' }, 2000)
  }
}

onMounted(() => {
  loadManifest()
})
</script>

<template>
  <div class="agent-connect-view">
    <h1>🔗 Agent 连接引导</h1>
    <p class="intro">
      将以下地址提供给 Agent，让它发现系统能力并完成接入。
      Agent 只需访问发现面即可获取全部契约，无需访问业务数据。
    </p>

    <!-- 发现面地址列表 -->
    <section class="card">
      <h2>📡 发现面地址</h2>
      <div v-for="item in connectUrls" :key="item.label" class="url-item">
        <div class="url-info">
          <span class="url-label">{{ item.label }}</span>
          <code class="url-value">{{ item.url }}</code>
          <span class="url-desc">{{ item.desc }}</span>
        </div>
        <button
          @click="copyUrl(item.url, item.label)"
          :class="['btn-copy', { copied: copiedField === item.label }]"
        >
          {{ copiedField === item.label ? '✅ 已复制' : '📋 复制' }}
        </button>
      </div>
    </section>

    <!-- Manifest 详情 -->
    <section v-if="!loading && manifest" class="card">
      <h2>📋 系统清单</h2>
      <dl class="manifest-details">
        <dt>服务名称</dt>
        <dd>{{ manifest.service?.name || '-' }}</dd>
        <dt>版本</dt>
        <dd>{{ manifest.service?.version || '-' }}</dd>
        <dt>数据策略</dt>
        <dd>
          <span class="badge" :class="manifest.data_policy?.discovery_contains_business_data ? 'bad' : 'good'">
            {{ manifest.data_policy?.discovery_contains_business_data ? '包含业务数据' : '不含业务数据' }}
          </span>
        </dd>
        <dt>认证方式</dt>
        <dd>{{ manifest.data_policy?.authentication || '-' }}</dd>
      </dl>

      <h3>能力列表</h3>
      <div v-if="manifest.capabilities?.length" class="capabilities">
        <div v-for="cap in manifest.capabilities" :key="cap.id" class="capability-item">
          <strong>{{ cap.name }}</strong>
          <p>{{ cap.description }}</p>
          <span v-for="s in cap.scopes" :key="s" class="scope-tag">{{ s }}</span>
        </div>
      </div>
    </section>

    <div v-if="loading" class="loading">加载中...</div>
    <div v-if="error" class="error">❌ {{ error }}</div>

    <!-- 引导步骤 -->
    <section class="card">
      <h2>📖 接入流程</h2>
      <ol class="steps">
        <li>复制上方任一发现面地址提供给 Agent</li>
        <li>Agent 访问 <code>/agent/bootstrap.md</code> 获取初始化说明</li>
        <li>Agent 通过 <code>/agent/skills/index.json</code> 了解可用技能</li>
        <li>前往 <RouterLink to="/agent-authorization">授权管理</RouterLink> 创建授权并签发 Token</li>
        <li>将 Token 配置到 Agent 环境变量 <code>BOOKSHELF_TOKEN</code></li>
        <li>Agent 即可通过 Bearer Token 访问业务 API</li>
      </ol>
    </section>
  </div>
</template>

<style scoped>
.agent-connect-view {
  max-width: 800px;
  margin: 0 auto;
  padding: 1rem;
}
.intro {
  color: var(--text-secondary, #666);
  margin-bottom: 1.5rem;
}
.card {
  background: var(--bg-card, white);
  border: 1px solid var(--border-color, #e0e0e0);
  border-radius: 8px;
  padding: 1.25rem;
  margin-bottom: 1.5rem;
}
.card h2 {
  margin-bottom: 1rem;
}
.url-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  border-bottom: 1px solid var(--border-color, #e0e0e0);
}
.url-item:last-child {
  border-bottom: none;
}
.url-info {
  flex: 1;
}
.url-label {
  font-weight: 600;
  margin-right: 0.5rem;
}
.url-value {
  font-family: monospace;
  font-size: 0.875rem;
  color: var(--primary, #4a90d9);
}
.url-desc {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary, #999);
  margin-top: 0.25rem;
}
.btn-copy {
  padding: 0.4rem 1rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  cursor: pointer;
  background: #f5f5f5;
  white-space: nowrap;
}
.btn-copy.copied {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}
.manifest-details dt {
  font-weight: 600;
  margin-top: 0.5rem;
}
.manifest-details dd {
  margin-left: 0;
  margin-bottom: 0.5rem;
}
.badge {
  font-size: 0.75rem;
  padding: 0.1rem 0.5rem;
  border-radius: 3px;
}
.badge.good {
  background: #e8f5e9;
  color: #2e7d32;
}
.badge.bad {
  background: #ffebee;
  color: #c62828;
}
.capabilities {
  display: grid;
  gap: 0.75rem;
}
.capability-item {
  padding: 0.75rem;
  border: 1px solid var(--border-color, #e0e0e0);
  border-radius: 6px;
}
.capability-item p {
  font-size: 0.875rem;
  color: var(--text-secondary, #666);
  margin: 0.25rem 0;
}
.scope-tag {
  display: inline-block;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  margin: 0.1rem;
  background: #e3f2fd;
  border-radius: 3px;
}
.steps {
  padding-left: 1.5rem;
  line-height: 2;
}
.steps code {
  background: #f0f0f0;
  padding: 0.1rem 0.3rem;
  border-radius: 3px;
  font-size: 0.875rem;
}
.loading, .error {
  text-align: center;
  padding: 1rem;
}
.error {
  color: #c62828;
}
</style>
