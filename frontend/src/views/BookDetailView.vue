<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useApiStore, coverUrl } from '@/stores/api'
import { useMembersStore } from '@/stores/members'
import { READING_STATUSES, statusLabel } from '@/types/models'
import type { BookDetail } from '@/types/models'

const props = defineProps<{ id: string }>()
const router = useRouter()
const api = useApiStore()
const members = useMembersStore()

const book = ref<BookDetail | null>(null)
const loading = ref(true)
const activeTab = ref('progress')

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
  loading.value = true
  try {
    book.value = await api.get<BookDetail>(`/books/${props.id}`)
    // 预填进度表单：取当前成员的进度
    const existing = book.value.reading_progress.find(
      (p) => p.member_id === members.selectedId,
    )
    if (existing) {
      progressForm.status = existing.status
      progressForm.current_page = existing.current_page
      progressForm.rating = existing.rating
    }
  } finally {
    loading.value = false
  }
}

const coverUrlForDetail = computed(() => coverUrl(book.value?.cover_path || null))
const placeholderChar = computed(() => book.value?.title?.charAt(0) || '?')
const placeholderColor = computed(() => {
  if (!book.value) return 'hsl(0,45%,55%)'
  const hash = book.value.title.split('').reduce((a, c) => a + c.charCodeAt(0), 0)
  return `hsl(${hash % 360}, 45%, 55%)`
})

async function submitProgress() {
  if (!members.selectedId) {
    alert('请先在顶栏选择成员')
    return
  }
  progressSaving.value = true
  try {
    await api.post(`/books/${props.id}/progress`, {
      member_id: members.selectedId,
      status: progressForm.status,
      current_page: progressForm.current_page,
      rating: progressForm.rating,
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
    alert('请先在顶栏选择成员')
    return
  }
  if (!noteForm.content_md.trim()) return
  noteSaving.value = true
  try {
    await api.post(`/books/${props.id}/notes`, {
      member_id: members.selectedId,
      note_type: 'excerpt',
      content_md: noteForm.content_md,
      page: noteForm.page,
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
</script>

<template>
  <div>
    <a href="javascript:void(0)" class="back-link" @click="router.push('/')">← 返回书架</a>

    <div v-if="loading" class="loading">加载中...</div>

    <div v-else-if="book" class="detail-layout">
      <!-- 左栏：封面 + 基本信息 -->
      <div>
        <img v-if="coverUrlForDetail" :src="coverUrlForDetail" class="detail-cover" :alt="book.title" />
        <div v-else class="detail-cover-placeholder" :style="{ background: placeholderColor }">
          {{ placeholderChar }}
        </div>
      </div>

      <!-- 右栏：信息 + Tab -->
      <div>
        <h1 class="detail-title">{{ book.title }}</h1>
        <div v-if="book.subtitle" style="color: var(--text-muted); margin-bottom: 4px;">
          {{ book.subtitle }}
        </div>
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

        <p v-if="book.summary" style="font-size: 14px; color: var(--text); margin-bottom: 16px;">
          {{ book.summary }}
        </p>

        <!-- Tabs -->
        <div class="tabs">
          <button
            v-for="tab in [
              { key: 'progress', label: '阅读进度' },
              { key: 'copies', label: '副本' },
              { key: 'purchases', label: '购买' },
              { key: 'notes', label: '笔记' },
              { key: 'attachments', label: '附件' },
              { key: 'custom', label: '自定义' },
            ]"
            :key="tab.key"
            class="tab"
            :class="{ active: activeTab === tab.key }"
            @click="activeTab = tab.key"
          >
            {{ tab.label }}
          </button>
        </div>

        <!-- 阅读进度 -->
        <div v-if="activeTab === 'progress'">
          <table v-if="book.reading_progress.length" class="data-table">
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
                <td style="font-size: 12px; color: var(--text-muted);">{{ p.updated_at.slice(0, 10) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else style="color: var(--text-muted); font-size: 14px;">暂无阅读进度</p>

          <h3 style="margin: 20px 0 8px; font-size: 15px;">更新进度</h3>
          <div class="inline-form">
            <div>
              <label>状态</label><br/>
              <select v-model="progressForm.status">
                <option v-for="s in READING_STATUSES.filter(s => s.value)" :key="s.value" :value="s.value">
                  {{ s.label }}
                </option>
              </select>
            </div>
            <div>
              <label>当前页</label><br/>
              <input v-model.number="progressForm.current_page" type="number" placeholder="页码" style="width: 80px;" />
            </div>
            <div>
              <label>评分</label><br/>
              <select v-model.number="progressForm.rating">
                <option :value="null">未评</option>
                <option v-for="n in 5" :key="n" :value="n">{{ '⭐'.repeat(n) }}</option>
              </select>
            </div>
            <button class="btn" :disabled="progressSaving" @click="submitProgress">
              {{ progressSaving ? '保存中...' : '保存' }}
            </button>
          </div>
        </div>

        <!-- 副本 -->
        <div v-if="activeTab === 'copies'">
          <table v-if="book.copies.length" class="data-table">
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
          <p v-else style="color: var(--text-muted); font-size: 14px;">暂无副本记录</p>
        </div>

        <!-- 购买 -->
        <div v-if="activeTab === 'purchases'">
          <table v-if="book.purchase_records.length" class="data-table">
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
          <p v-else style="color: var(--text-muted); font-size: 14px;">暂无购买记录</p>
        </div>

        <!-- 笔记 -->
        <div v-if="activeTab === 'notes'">
          <div v-for="n in book.reading_notes" :key="n.id" style="background: var(--card-bg); padding: 12px; border-radius: 6px; margin-bottom: 8px;">
            <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 4px;">
              {{ n.note_type }}
              <span v-if="n.page"> · 第 {{ n.page }} 页</span>
              <span v-if="n.chapter"> · {{ n.chapter }}</span>
              · {{ n.updated_at.slice(0, 10) }}
            </div>
            <div style="font-size: 14px; white-space: pre-wrap;">{{ n.content_md }}</div>
          </div>
          <p v-if="!book.reading_notes.length" style="color: var(--text-muted); font-size: 14px;">暂无笔记</p>

          <h3 style="margin: 20px 0 8px; font-size: 15px;">添加笔记</h3>
          <div class="inline-form" style="flex-direction: column; align-items: stretch;">
            <textarea
              v-model="noteForm.content_md"
              placeholder="摘录或感想..."
              style="width: 100%; min-height: 80px; padding: 8px;"
            ></textarea>
            <div style="display: flex; gap: 8px; margin-top: 8px;">
              <input v-model.number="noteForm.page" type="number" placeholder="页码（可选）" style="width: 100px;" />
              <button class="btn" :disabled="noteSaving || !noteForm.content_md.trim()" @click="submitNote">
                {{ noteSaving ? '保存中...' : '添加笔记' }}
              </button>
            </div>
          </div>
        </div>

        <!-- 附件 -->
        <div v-if="activeTab === 'attachments'">
          <div v-for="a in book.attachments" :key="a.id" style="background: var(--card-bg); padding: 10px; border-radius: 6px; margin-bottom: 6px; font-size: 14px;">
            <span style="color: var(--text-muted); font-size: 12px;">[{{ a.attach_type }}]</span>
            {{ a.title || a.url || a.content_md?.slice(0, 50) || '附件' }}
            <a v-if="a.url" :href="a.url" target="_blank" style="color: var(--primary); margin-left: 8px;">打开 ↗</a>
          </div>
          <p v-if="!book.attachments.length" style="color: var(--text-muted); font-size: 14px;">暂无附件</p>
        </div>

        <!-- 自定义字段 -->
        <div v-if="activeTab === 'custom'">
          <table v-if="book.custom_fields.length" class="data-table">
            <tbody>
              <tr v-for="f in book.custom_fields" :key="f.id">
                <td style="font-weight: 600;">{{ f.field_key }}</td>
                <td>{{ f.field_value || '-' }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else style="color: var(--text-muted); font-size: 14px;">暂无自定义字段</p>
        </div>
      </div>
    </div>
  </div>
</template>
