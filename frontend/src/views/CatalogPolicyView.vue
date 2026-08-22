<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { sessionRole } from '@/stores/session'
import { lastError } from '@/stores/api'

/**
 * 访问策略页（权限阶段 4，基线 §10.7）：Owner 查看匿名目录模式与 C→B 切换预览，
 * 批量设置逐书可见级别。模式本身由部署配置 ANONYMOUS_CATALOG_MODE 切换
 * （重启生效），本页不做运行时切换。
 */
const BASE = `${import.meta.env.BASE_URL}api/v1`
const loading = ref(true)
const preview = ref<any>(null)
const busy = ref(false)
const notice = ref('')

const VIS_LABELS: Record<string, string> = {
  lan_shared: '局域网共享（C 模式默认）',
  public: '明确公开（B 模式）',
  members_only: '仅家庭成员',
  private: '私有（不匿名展示）',
}

async function api(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-UI-Client': 'web', ...(options?.headers || {}) },
    ...options,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok || body.ok === false) throw new Error(body.detail || body.error || `HTTP ${res.status}`)
  return body.data
}

async function loadPreview() {
  loading.value = true
  try {
    preview.value = await api('/catalog-visibility/preview')
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    loading.value = false
  }
}

async function batchSet(ids: number[], visibility: string, label: string) {
  if (!ids.length) return
  if (!confirm(`将 ${ids.length} 本书设为「${label}」？`)) return
  busy.value = true
  try {
    const r = await api('/catalog-visibility/batch', {
      method: 'POST',
      body: JSON.stringify({ book_ids: ids, visibility }),
    })
    notice.value = `已更新 ${r.changed} 本（请求 ${r.requested} 本）`
    await loadPreview()
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = false
  }
}

onMounted(loadPreview)
</script>

<template>
  <section class="policy-view" v-if="sessionRole === 'owner'">
    <h1>访问策略</h1>
    <p class="muted">
      匿名目录模式由部署配置 <code>ANONYMOUS_CATALOG_MODE</code> 切换（lan_shared /
      explicit_public / disabled，重启生效）；本页管理逐书可见级别并提供 C→B 切换预览。
      切换模式不改写书目数据，回滚 = 把配置切回原值。
    </p>

    <div v-if="loading">加载中…</div>
    <template v-else-if="preview">
      <div class="cards">
        <div class="card">
          <h3>当前模式</h3>
          <p class="mode">{{ preview.current_mode }}</p>
        </div>
        <div class="card">
          <h3>切换到 explicit_public 后</h3>
          <p>继续公开 <strong>{{ preview.summary.remain_public }}</strong> 本 ·
            从匿名书架消失 <strong>{{ preview.summary.disappear_from_anonymous }}</strong> 本 ·
            任何模式都不可匿名见 <strong>{{ preview.summary.never_anonymous }}</strong> 本</p>
        </div>
      </div>

      <h2>将继续公开（public）</h2>
      <p v-if="preview.truncated" class="warn">书目较多，每个列表最多显示 500 条（计数统计为全量）</p>
      <ul class="book-list">
        <li v-for="b in preview.remain_public" :key="b.id">{{ b.title }}（#{{ b.id }}）</li>
        <li v-if="!preview.remain_public.length" class="muted">暂无明确公开的书目</li>
      </ul>
      <button
        class="ghost"
        :disabled="busy || !preview.disappear.length"
        @click="batchSet(preview.disappear.map((b: any) => b.id), 'public', VIS_LABELS.public)"
      >将下面「将消失」的书全部设为明确公开</button>

      <h2>将从匿名书架消失（未标记 public）</h2>
      <ul class="book-list">
        <li v-for="b in preview.disappear" :key="b.id">{{ b.title }}（#{{ b.id }}，当前 {{ VIS_LABELS[b.visibility] || b.visibility }}）</li>
        <li v-if="!preview.disappear.length" class="muted">无（全部已标记 public）</li>
      </ul>
      <button
        class="ghost danger"
        :disabled="busy || !preview.remain_public.length"
        @click="batchSet(preview.remain_public.map((b: any) => b.id), 'lan_shared', VIS_LABELS.lan_shared)"
      >将全部 public 退回局域网共享</button>

      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    </template>
  </section>
  <section v-else class="policy-view">
    <p>此页面仅 Owner 可用。</p>
  </section>
</template>

<style scoped>
.policy-view { max-width: 860px; margin: 0 auto; padding: var(--space-3, 12px); }
.muted { color: var(--text-muted, #5a6878); font-size: 0.92rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 16px 0; }
.card { background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 12px; padding: 16px; }
.card h3 { margin: 0 0 8px; font-size: 1rem; }
.mode { font-size: 1.2rem; font-weight: 600; margin: 0; }
h2 { font-size: 1.05rem; margin: 20px 0 8px; }
.book-list { list-style: none; padding: 0; margin: 0 0 8px; max-height: 280px; overflow-y: auto;
  background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; }
.book-list li { padding: 8px 14px; border-bottom: 1px solid var(--border, #eef2f6); font-size: 0.92rem; }
.book-list li:last-child { border-bottom: none; }
button { margin: 6px 0; padding: 8px 14px; border-radius: 8px; cursor: pointer; }
.ghost { background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit; }
.ghost.danger { color: #b3261e; }
.warn { color: #8a5a19; font-size: 0.88rem; }
.notice { color: #1c6b48; }
</style>
