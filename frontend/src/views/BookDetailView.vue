<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useApiStore, coverUrl, safeUrl, attachmentUrl } from '@/stores/api'
import { useMembersStore } from '@/stores/members'
import { READING_STATUSES, statusLabel } from '@/types/models'
import type { BookDetail, Attachment } from '@/types/models'

const props = defineProps<{ id: string }>()
const router = useRouter()
const route = useRoute()
const api = useApiStore()
const members = useMembersStore()

const book = ref<BookDetail | null>(null)
const loading = ref(true)
const loadError = ref<string | null>(null)  // BUG-125：加载失败时展示错误状态 + 重试

// BUG-120：请求代际跟踪，丢弃过期的异步响应（如快速切换书籍/成员时）
let requestGen = 0

// Tab 定义
const tabs = [
  { key: 'progress', label: '阅读进度' },
  { key: 'copies', label: '副本' },
  { key: 'purchases', label: '购买' },
  { key: 'notes', label: '笔记' },
  { key: 'attachments', label: '附件' },
  { key: 'custom', label: '自定义' },
] as const

// 修复 P2：Tab 状态持久化到 URL hash，刷新后恢复
const activeTab = ref<string>((route.hash || '#progress').slice(1))
const validTabKeys = tabs.map((t) => t.key)
if (!validTabKeys.includes(activeTab.value as any)) {
  activeTab.value = 'progress'
}

function selectTab(key: string) {
  activeTab.value = key
  // 同步到 URL hash（不触发滚动）；仅传 hash，避免 spread route 带入 matched/meta 等只读字段
  router.replace({ hash: `#${key}` })
}

// 修复 P1：Tab 键盘导航（左右箭头切换）
function onTabKeydown(e: KeyboardEvent, index: number) {
  let nextIndex: number | null = null
  if (e.key === 'ArrowRight') nextIndex = (index + 1) % tabs.length
  else if (e.key === 'ArrowLeft') nextIndex = (index - 1 + tabs.length) % tabs.length
  else if (e.key === 'Home') nextIndex = 0
  else if (e.key === 'End') nextIndex = tabs.length - 1

  if (nextIndex !== null) {
    e.preventDefault()
    selectTab(tabs[nextIndex].key)
    // 移动焦点到新激活的 tab
    nextTick(() => {
      const tabEl = document.getElementById(`tab-${tabs[nextIndex!].key}`)
      tabEl?.focus()
    })
  }
}

// 是否有成员选中（用于禁用进度/笔记表单）
const hasMember = computed(() => members.selectedId != null)

// 改进度表单
const progressForm = reactive({
  status: 'reading',
  current_page: null as number | null,
  rating: null as number | null,
})
const progressSaving = ref(false)

// 加笔记表单
const noteForm = reactive({
  content_md: '',
  page: null as number | null,
})
const noteSaving = ref(false)

async function loadDetail() {
  const gen = ++requestGen  // BUG-120：递增代际，过期响应将被丢弃
  loading.value = true
  loadError.value = null
  try {
    const data = await api.get<BookDetail>(`/books/${props.id}`)
    if (gen !== requestGen) return  // 丢弃过期响应
    book.value = data
    // 预填进度表单：取当前成员的进度
    const existing = book.value.reading_progress.find(
      (p) => p.member_id === members.selectedId,
    )
    if (existing) {
      progressForm.status = existing.status
      progressForm.current_page = existing.current_page
      progressForm.rating = existing.rating
    } else {
      // 修复 BUG：未命中当前成员进度时重置表单，避免残留上一书/上一成员数据
      progressForm.status = 'reading'
      progressForm.current_page = null
      progressForm.rating = null
    }
  } catch (e) {
    if (gen !== requestGen) return  // 丢弃过期错误
    loadError.value = e instanceof Error ? e.message : '加载失败'
  } finally {
    if (gen === requestGen) loading.value = false
  }
}

const coverUrlForDetail = computed(() => coverUrl(book.value?.cover_path || null))

// 修复 BUG：file 类附件通过 attachmentUrl 生成打开链接，不只依赖 a.url
function attachmentHref(a: Attachment): string | null {
  return safeUrl(a.url) || attachmentUrl(a.file_path)
}
const placeholderChar = computed(() => book.value?.title?.charAt(0) || '?')

// BUG-124：v-model.number 清空输入框在运行时产生空字符串 ""，
// 非数字输入可能产生 NaN，部分边界还可能出现 undefined。
// 统一归一：null/undefined/""/0/NaN 均返回 null（页码 0 无意义），
// 确保允许显式清空当前页/评分且不向后端发送非法值。
function normalizeNumberInput(v: unknown): number | null {
  if (v === null || v === undefined || v === '' || v === 0) {
    return null
  }
  const n = Number(v)
  return Number.isNaN(n) ? null : n
}
// 修复 P2：占位符用 CSS 变量传 hue，sat/light 由全局 --cover-sat/--cover-light 决定（暗色模式自适应）
const placeholderHue = computed(() => {
  if (!book.value) return 0
  const hash = book.value.title.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return hash % 360
})

async function submitProgress() {
  if (!members.selectedId) {
    return
  }
  progressSaving.value = true
  try {
    // BUG-124：v-model.number 清空输入框会产生空字符串 ""，须归一为 null；
    // 0 也归一为 null（页码 0 无意义），确保允许显式清空当前页/评分
    const page = normalizeNumberInput(progressForm.current_page)
    const rating = normalizeNumberInput(progressForm.rating)
    await api.post(`/books/${props.id}/progress`, {
      member_id: members.selectedId,
      status: progressForm.status,
      current_page: page,
      rating: rating,
    })
    await loadDetail()
  } catch {
    // 错误已由 api store 设置到 lastError
  } finally {
    progressSaving.value = false
  }
}

async function submitNote() {
  if (!members.selectedId) {
    return
  }
  if (!noteForm.content_md.trim()) return
  noteSaving.value = true
  try {
    // BUG-124：v-model.number 清空产生空字符串 ""，归一为 null 避免后端 422
    const page = normalizeNumberInput(noteForm.page)
    await api.post(`/books/${props.id}/notes`, {
      member_id: members.selectedId,
      note_type: 'excerpt',
      content_md: noteForm.content_md,
      page: page,
    })
    noteForm.content_md = ''
    noteForm.page = null
    await loadDetail()
  } catch {
    // 错误已由 api store 处理
  } finally {
    noteSaving.value = false
  }
}

onMounted(loadDetail)
watch(() => props.id, loadDetail)
// 修复 4.3：切换成员后刷新详情页进度表（进度按成员展示）
watch(() => members.selectedId, loadDetail)
</script>

<template>
  <div>
    <!-- 修复 P1：返回链接改用 button 语义 -->
    <button class="back-link" @click="router.push('/')">← 返回书架</button>

    <!-- 骨架屏（修复 P2） -->
    <div v-if="loading" class="detail-layout" aria-hidden="true">
      <div class="skeleton-detail-cover"></div>
      <div>
        <div class="skeleton-text-line" style="width: 70%; height: 28px;"></div>
        <div class="skeleton-text-line" style="width: 40%;"></div>
        <div class="skeleton-text-line" style="width: 90%;"></div>
        <div class="skeleton-text-line" style="width: 85%;"></div>
        <div class="skeleton-text-line" style="width: 60%;"></div>
      </div>
    </div>

    <!-- BUG-125：加载失败时展示错误状态 + 重试按钮 -->
    <div v-else-if="loadError" class="error-state">
      <p class="error-state-msg">{{ loadError }}</p>
      <button class="btn" @click="loadDetail">重试</button>
    </div>

    <div v-else-if="book" class="detail-layout">
      <!-- 左栏：封面 + 基本信息 -->
      <div>
        <img v-if="coverUrlForDetail" :src="coverUrlForDetail" class="detail-cover" :alt="book.title" />
        <div
          v-else
          class="detail-cover-placeholder"
          :style="{ '--cover-hue': placeholderHue }"
          role="img"
          :aria-label="`${book.title}（无封面）`"
        >
          {{ placeholderChar }}
        </div>
      </div>

      <!-- 右栏：信息 + Tab -->
      <div>
        <h1 class="detail-title">{{ book.title }}</h1>
        <div v-if="book.subtitle" class="detail-subtitle">{{ book.subtitle }}</div>
        <div class="detail-meta">
          <span v-if="book.authors?.length">{{ book.authors.join(', ') }}</span>
          <span v-if="book.publisher"> · {{ book.publisher }}</span>
          <span v-if="book.publish_date"> · {{ book.publish_date }}</span>
        </div>
        <div class="detail-meta">
          <span v-if="book.isbn13">ISBN: {{ book.isbn13 }}</span>
          <span v-if="book.page_count"> · {{ book.page_count }} 页</span>
          <span v-if="book.category"> · {{ book.category }}</span>
        </div>

        <div v-if="book.tags.length" class="detail-tags">
          <span v-for="tag in book.tags" :key="tag" class="tag">{{ tag }}</span>
        </div>

        <p v-if="book.summary" class="detail-summary">{{ book.summary }}</p>

        <!-- Tabs（修复 P1：ARIA tablist + 键盘导航） -->
        <div class="tabs" role="tablist" aria-label="书籍详情">
          <button
            v-for="(tab, index) in tabs"
            :id="`tab-${tab.key}`"
            :key="tab.key"
            class="tab"
            :class="{ active: activeTab === tab.key }"
            role="tab"
            :aria-selected="activeTab === tab.key"
            :aria-controls="`panel-${tab.key}`"
            :tabindex="activeTab === tab.key ? 0 : -1"
            @click="selectTab(tab.key)"
            @keydown="onTabKeydown($event, index)"
          >
            {{ tab.label }}
          </button>
        </div>

        <!-- 阅读进度 -->
        <div
          v-if="activeTab === 'progress'"
          id="panel-progress"
          role="tabpanel"
          :aria-labelledby="`tab-progress`"
        >
          <div v-if="book.reading_progress.length" class="table-wrap">
            <table class="data-table">
              <thead>
                <tr>
                  <th>成员</th><th>状态</th><th>当前页</th><th>进度</th><th>评分</th><th>更新</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in book.reading_progress" :key="p.id">
                  <td>{{ members.members.find(m => m.id === p.member_id)?.name || p.member_id }}</td>
                  <td>{{ statusLabel(p.status) }}</td>
                  <td>{{ p.current_page || '-' }}</td>
                  <td>{{ p.percent != null ? p.percent + '%' : '-' }}</td>
                  <td>{{ p.rating ? '⭐'.repeat(p.rating) : '-' }}</td>
                  <td class="cell-meta">{{ p.updated_at.slice(0, 10) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted-text">暂无阅读进度</p>

          <h3 class="section-title" style="margin-top: 20px;">更新进度</h3>
          <!-- 修复 4.4：无成员时显示提示 -->
          <p v-if="!hasMember" class="member-hint">请先在顶栏选择成员后再更新进度</p>
          <div class="inline-form">
            <div>
              <label for="pf-status">状态</label>
              <select id="pf-status" v-model="progressForm.status" :disabled="!hasMember">
                <option v-for="s in READING_STATUSES.filter(s => s.value)" :key="s.value" :value="s.value">
                  {{ s.label }}
                </option>
              </select>
            </div>
            <div>
              <label for="pf-page">当前页</label>
              <input id="pf-page" v-model.number="progressForm.current_page" type="number" min="0" :max="book.page_count || undefined" placeholder="页码" style="width: 80px;" :disabled="!hasMember" />
            </div>
            <div>
              <label for="pf-rating">评分</label>
              <select id="pf-rating" v-model.number="progressForm.rating" :disabled="!hasMember">
                <option :value="null">未评</option>
                <option v-for="n in 5" :key="n" :value="n">{{ '⭐'.repeat(n) }}</option>
              </select>
            </div>
            <button
              class="btn"
              :disabled="progressSaving || !hasMember"
              @click="submitProgress"
            >
              {{ progressSaving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>

        <!-- 副本 -->
        <div
          v-if="activeTab === 'copies'"
          id="panel-copies"
          role="tabpanel"
          aria-labelledby="tab-copies"
        >
          <div v-if="book.copies.length" class="table-wrap">
            <table class="data-table">
              <thead><tr><th>位置</th><th>类型</th><th>状态</th><th>格式</th></tr></thead>
              <tbody>
                <tr v-for="c in book.copies" :key="c.id">
                  <td>{{ c.location || '-' }}</td>
                  <td>{{ c.copy_type }}</td>
                  <td>{{ c.status }}</td>
                  <td>{{ c.format || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted-text">暂无副本记录</p>
        </div>

        <!-- 购买 -->
        <div
          v-if="activeTab === 'purchases'"
          id="panel-purchases"
          role="tabpanel"
          aria-labelledby="tab-purchases"
        >
          <div v-if="book.purchase_records.length" class="table-wrap">
            <table class="data-table">
              <thead><tr><th>价格</th><th>原价</th><th>渠道</th><th>订单号</th><th>日期</th></tr></thead>
              <tbody>
                <tr v-for="p in book.purchase_records" :key="p.id">
                  <td>{{ p.currency === 'CNY' ? '¥' : p.currency + ' ' }}{{ p.price }}</td>
                  <td>{{ p.original_price || '-' }}</td>
                  <td>{{ p.channel || '-' }}</td>
                  <td>{{ p.order_no || '-' }}</td>
                  <td>{{ p.purchase_date || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted-text">暂无购买记录</p>
        </div>

        <!-- 笔记 -->
        <div
          v-if="activeTab === 'notes'"
          id="panel-notes"
          role="tabpanel"
          aria-labelledby="tab-notes"
        >
          <div v-for="n in book.reading_notes" :key="n.id" class="note-card">
            <div class="note-meta">
              {{ n.note_type }}
              <span v-if="n.page"> · 第 {{ n.page }} 页</span>
              <span v-if="n.chapter"> · {{ n.chapter }}</span>
              · {{ n.updated_at.slice(0, 10) }}
            </div>
            <div class="note-content">{{ n.content_md }}</div>
          </div>
          <p v-if="!book.reading_notes.length" class="muted-text">暂无笔记</p>

          <h3 class="section-title" style="margin-top: 20px;">添加笔记</h3>
          <p v-if="!hasMember" class="member-hint">请先在顶栏选择成员后再添加笔记</p>
          <div class="inline-form note-form">
            <label for="note-content" class="sr-only">笔记内容</label>
            <textarea
              id="note-content"
              v-model="noteForm.content_md"
              placeholder="摘录或感想..."
              :disabled="!hasMember"
            ></textarea>
            <div class="note-form-actions">
              <label for="note-page" class="sr-only">页码（可选）</label>
              <input id="note-page" v-model.number="noteForm.page" type="number" placeholder="页码（可选）" :disabled="!hasMember" />
              <button
                class="btn"
                :disabled="noteSaving || !noteForm.content_md.trim() || !hasMember"
                @click="submitNote"
              >
                {{ noteSaving ? '保存中...' : '添加笔记' }}
              </button>
            </div>
          </div>
        </div>

        <!-- 附件 -->
        <div
          v-if="activeTab === 'attachments'"
          id="panel-attachments"
          role="tabpanel"
          aria-labelledby="tab-attachments"
        >
          <div v-for="a in book.attachments" :key="a.id" class="attachment-row">
            <span class="attachment-type">[{{ a.attach_type }}]</span>
            {{ a.title || a.url || a.file_path || a.content_md?.slice(0, 50) || '附件' }}
            <a v-if="attachmentHref(a)" :href="attachmentHref(a) || undefined" target="_blank" rel="noopener noreferrer" class="link-primary" style="margin-left: 8px;">打开 ↗</a>
          </div>
          <p v-if="!book.attachments.length" class="muted-text">暂无附件</p>
        </div>

        <!-- 自定义字段 -->
        <div
          v-if="activeTab === 'custom'"
          id="panel-custom"
          role="tabpanel"
          aria-labelledby="tab-custom"
        >
          <div v-if="book.custom_fields.length" class="table-wrap">
            <table class="data-table">
              <tbody>
                <tr v-for="f in book.custom_fields" :key="f.id">
                  <td style="font-weight: 600;">{{ f.field_key }}</td>
                  <td>{{ f.field_value || '-' }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="muted-text">暂无自定义字段</p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 屏幕阅读器专用 */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* 修复 P2：占位符饱和度提升 + 暗色模式自适应（sat/light 取自全局 token） */
.detail-cover-placeholder {
  background: hsl(var(--cover-hue, 0), var(--cover-sat, 62%), var(--cover-light, 36%));
}

.member-hint {
  color: var(--warning);
  background: var(--warning-bg);
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  margin-bottom: 12px;
}

.note-form {
  flex-direction: column;
  align-items: stretch;
}
.note-form textarea {
  width: 100%;
  min-height: 80px;
  padding: 8px;
  resize: vertical;
}
.note-form-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
}
.note-form-actions input {
  width: 100px;
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
