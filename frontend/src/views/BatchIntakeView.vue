<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { sessionRole } from '@/stores/session'
import { lastError, extractApiErrorMessage } from '@/stores/api'

/**
 * Owner 拍照批量入库核对工作台（PLN-012 M3）。
 *
 * 流程：上传多图 →（模型识别 / 手工填写）→ 候选核对（编辑/拆分/关联照片）
 * → 既有书匹配预览 → 确认选中 → 执行 → 回执/重试。
 * 刷新/重启后重新进入页面即可恢复：全部状态在服务端协作对象里。
 */
const BASE = `${import.meta.env.BASE_URL}api/v1`

interface Photo { id: number; photo_id: string; role: string; image_url: string }
interface Candidate {
  id: number
  version: number
  status: string
  title: string | null
  subtitle: string | null
  authors: string[]
  isbn: string | null
  photo_ids: string[]
  match_book_id: number | null
  match_diff: Record<string, { candidate: unknown; existing: unknown }> | null
}
interface Execution {
  id: number
  status: string
  book_id: number | null
  error_code: string | null
  error: string | null
}
interface WorkItem {
  id: number
  title: string
  status: string
  photos: Photo[]
  candidates: Candidate[]
  executions: Execution[]
}

const loading = ref(true)
const busy = ref('')
const notice = ref('')
const items = ref<{ id: number; title: string; status: string }[]>([])
const current = ref<WorkItem | null>(null)
const newTitle = ref('')
const selected = ref<Set<number>>(new Set())
interface EditState {
  title: string
  subtitle: string
  authors: string
  isbn: string
  /** 初始化时服务端内容指纹；快照刷新后指纹不一致说明服务端值变了，必须重置缓存。 */
  fingerprint: string
}
const editing = ref<Record<number, EditState>>({})

async function api(path: string, options?: RequestInit) {
  // BUG-249：FormData 请求不能带 JSON Content-Type，否则浏览器不会生成 multipart boundary，
  // 真实后端解析不到文件。FormData 时省略该头，交给浏览器处理。
  const headers: Record<string, string> = {}
  if (!(options?.body instanceof FormData)) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: { ...headers, ...(options?.headers || {}) },
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok || body.ok === false) {
    throw new Error(extractApiErrorMessage(body, res.status, '请求失败'))
  }
  return body.data
}

async function loadItems() {
  const data = await api('/intake-workflow/work-items')
  items.value = data.items
}

async function open(id: number) {
  loading.value = true
  current.value = null
  selected.value = new Set()
  try {
    const item = (await api(`/intake-workflow/work-items/${id}`)) as WorkItem
    current.value = item
    syncEdits(item.candidates)
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    loading.value = false
  }
}

async function createItem() {
  busy.value = 'create'
  notice.value = ''
  try {
    const data = await api('/intake-workflow/work-items', {
      method: 'POST',
      body: JSON.stringify({ title: newTitle.value || '' }),
    })
    newTitle.value = ''
    await loadItems()
    await open(data.id)
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

async function uploadPhotos(event: Event) {
  const input = event.target as HTMLInputElement
  if (!input.files?.length || !current.value) return
  busy.value = 'upload'
  notice.value = ''
  try {
    const form = new FormData()
    // BUG-269：编号必须从已有照片的最大序号续起，否则第二轮上传的 p0001.. 与已有 photo_id 冲突。
    // 视图内没有单张删除入口；仍按最大数字后缀取序号，与后端省略 photo_ids 时的续号规则一致。
    const maxSeq = current.value.photos.reduce((max, p) => {
      const m = /^p(\d+)$/.exec(p.photo_id)
      return m ? Math.max(max, parseInt(m[1], 10)) : max
    }, 0)
    form.append('photo_ids', Array.from(input.files).map((_, i) => `p${String(maxSeq + i + 1).padStart(4, '0')}`).join(','))
    for (const file of Array.from(input.files)) form.append('files', file)
    const data = await api(`/intake-workflow/work-items/${current.value.id}/photos`, { method: 'POST', body: form })
    await open(current.value.id)
    // BUG-249：后端可能 200 但 added 为空（未保存任何文件），此时必须报错而不是提示成功。
    if (!Array.isArray(data?.added) || data.added.length === 0) {
      lastError.value = '照片上传未保存任何文件，请重试'
    } else {
      notice.value = `已上传 ${data.added.length} 张照片，下一步识别或直接核对`
    }
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
    input.value = ''
  }
}

async function recognize() {
  if (!current.value) return
  busy.value = 'recognize'
  notice.value = ''
  try {
    const data = await api(`/intake-workflow/work-items/${current.value.id}/recognize`, { method: 'POST' })
    await open(current.value.id)
    notice.value = data.failures?.length
      ? `识别完成，${data.failures.length} 张失败（见照片警告）`
      : '识别完成，请逐条核对候选'
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

async function previewMatches() {
  if (!current.value) return
  busy.value = 'preview'
  try {
    await api(`/intake-workflow/work-items/${current.value.id}/preview-matches`, { method: 'POST' })
    await open(current.value.id)
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

function fingerprintOf(c: Candidate) {
  return JSON.stringify([c.title, c.subtitle, c.isbn, (c.authors || []).join('、')])
}

function initialEdit(c: Candidate): EditState {
  return {
    title: c.title || '',
    subtitle: c.subtitle || '',
    authors: (c.authors || []).join('、'),
    isbn: c.isbn || '',
    fingerprint: fingerprintOf(c),
  }
}

function editOf(c: Candidate) {
  if (!editing.value[c.id]) {
    editing.value[c.id] = initialEdit(c)
  }
  return editing.value[c.id]
}

// BUG-273：后端重建候选会复用 ID（SQLite），每次快照加载都按指纹对账：
// 服务端内容没变的候选保留人工改动；变了的（含重新识别出新值）丢弃旧缓存，避免"看着旧值确认新值"。
function syncEdits(candidates: Candidate[]) {
  const next: Record<number, EditState> = {}
  for (const c of candidates) {
    const prev = editing.value[c.id]
    next[c.id] = prev && prev.fingerprint === fingerprintOf(c) ? prev : initialEdit(c)
  }
  editing.value = next
}

function editPayload(edit: EditState): Record<string, unknown> {
  return {
    title: edit.title,
    subtitle: edit.subtitle,
    authors: edit.authors ? edit.authors.split(/[，,、;；]/).map((s) => s.trim()).filter(Boolean) : [],
    isbn: edit.isbn || '',
  }
}

// BUG-281：编辑态与服务端值比对（按保存后得到的规范化形态，ISBN 忽略
// 分隔符差异），判断是否存在未保存修改。
function hasUnsavedEdit(c: Candidate): boolean {
  const payload = editPayload(editOf(c)) as { title: string; subtitle: string; authors: string[]; isbn: string }
  const isbnNorm = payload.isbn.replace(/[^0-9Xx]/g, '').toUpperCase() || null
  return (
    (payload.title.trim() || null) !== (c.title ?? null)
    || payload.subtitle.trim() !== (c.subtitle ?? '')
    || JSON.stringify(payload.authors) !== JSON.stringify(c.authors || [])
    || isbnNorm !== (c.isbn ?? null)
  )
}

async function saveCandidate(c: Candidate) {
  busy.value = `save-${c.id}`
  try {
    const payload = editPayload(editOf(c))
    await api(`/intake-workflow/candidates/${c.id}`, { method: 'PATCH', body: JSON.stringify(payload) })
    await open(current.value!.id)
    notice.value = `候选已保存（版本 +1，需重新确认）`
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

async function splitCandidate(c: Candidate) {
  if (!current.value) return
  const photoIds = window.prompt(`要拆出哪些照片？逗号分隔（当前：${c.photo_ids.join(',')}）`)
  if (!photoIds) return
  busy.value = `split-${c.id}`
  try {
    await api(`/intake-workflow/candidates/${c.id}/split`, {
      method: 'POST',
      body: JSON.stringify({ photo_ids: photoIds.split(/[,，]/).map((s) => s.trim()).filter(Boolean) }),
    })
    await open(current.value.id)
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

function toggle(c: Candidate) {
  // BUG-282：软删除（rejected）与已执行候选仅供追溯，不进入确认流程
  if (c.status === 'executed' || c.status === 'rejected') return
  const next = new Set(selected.value)
  if (next.has(c.id)) next.delete(c.id)
  else next.add(c.id)
  selected.value = next
}

async function confirmSelected() {
  if (!current.value || selected.value.size === 0) return
  busy.value = 'confirm'
  notice.value = ''
  const itemId = current.value.id
  const ids = Array.from(selected.value)
  try {
    // BUG-281：确认生效的是服务端值。选中候选存在未保存修改时先逐条保存再
    // 确认，保证"所见即所存"；保存失败（如 ISBN 校验位错误）则中止确认并提示，
    // 不得再出现"页面显示新值、实际确认旧值"的成功假象。
    let savedCount = 0
    for (const id of ids) {
      const c = current.value.candidates.find((x) => x.id === id)
      if (!c || c.status === 'rejected' || !hasUnsavedEdit(c)) continue
      await api(`/intake-workflow/candidates/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(editPayload(editOf(c))),
      })
      savedCount++
    }
    await api(`/intake-workflow/work-items/${itemId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ candidate_ids: ids }),
    })
    await open(itemId)
    notice.value = savedCount > 0
      ? `已保存 ${savedCount} 条修改并确认 ${ids.length} 条，可以执行`
      : `已确认 ${ids.length} 条，可以执行`
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

async function execute() {
  if (!current.value) return
  busy.value = 'execute'
  notice.value = ''
  try {
    const data = await api(`/intake-workflow/work-items/${current.value.id}/execute`, { method: 'POST' })
    await open(current.value.id)
    notice.value = `执行完成：${statusText[data.status] || data.status}`
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

async function retry(executionId: number) {
  busy.value = `retry-${executionId}`
  try {
    await api(`/intake-workflow/executions/${executionId}/retry`, { method: 'POST' })
    await open(current.value!.id)
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    busy.value = ''
  }
}

const statusText: Record<string, string> = {
  draft: '草稿', in_review: '核对中', confirmed: '已确认', executing: '执行中',
  completed: '已完成', partial: '部分完成', failed: '失败', cancelled: '已取消',
  pending_review: '待核对', executed: '已入库', rejected: '已拒绝',
}

onMounted(async () => {
  if (sessionRole.value !== 'owner') {
    loading.value = false
    return
  }
  try {
    await loadItems()
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="batch-intake" v-if="sessionRole === 'owner'">
    <h1>拍照批量入库</h1>
    <p class="muted">
      上传封面照片 → 核对候选书目（模型识别或手工填写）→ 匹配已有书预览 →
      确认选中条目 → 执行入库。确认过的字段不会被外部元数据覆盖；
      重复执行不会重复建书。刷新页面可随时恢复进度。
    </p>

    <div class="toolbar">
      <input v-model="newTitle" type="text" placeholder="新批次名称，如：客厅书架 10 月" maxlength="200" />
      <button class="btn" :disabled="busy === 'create'" @click="createItem">新建批次</button>
    </div>

    <p v-if="loading">加载中…</p>

    <ul v-if="!current && items.length" class="item-list">
      <li v-for="it in items" :key="it.id">
        <button class="link" @click="open(it.id)">
          #{{ it.id }} {{ it.title }} · {{ statusText[it.status] || it.status }}
        </button>
      </li>
    </ul>
    <p v-if="!current && !items.length && !loading" class="muted">还没有批次，先新建一个。</p>

    <div v-if="current" class="workbench">
      <header class="wb-head">
        <button class="link" @click="current = null; loadItems()">← 返回列表</button>
        <h2>#{{ current.id }} {{ current.title }} · {{ statusText[current.status] || current.status }}</h2>
      </header>

      <div class="actions">
        <label class="btn upload">
          {{ busy === 'upload' ? '上传中…' : '上传照片' }}
          <input type="file" accept="image/*" multiple :disabled="busy !== ''" @change="uploadPhotos" />
        </label>
        <button class="btn" :disabled="busy !== '' || !current.photos.length" @click="recognize">
          {{ busy === 'recognize' ? '识别中…' : '模型识别（需先在“模型”页启用）' }}
        </button>
        <button class="btn" :disabled="busy !== '' || !current.candidates.length" @click="previewMatches">
          匹配已有书
        </button>
        <button class="btn primary" :disabled="busy !== '' || selected.size === 0" @click="confirmSelected">
          确认选中（{{ selected.size }}）
        </button>
        <button class="btn primary" :disabled="busy !== '' || current.status !== 'confirmed'" @click="execute">
          {{ busy === 'execute' ? '执行中…' : '执行入库' }}
        </button>
      </div>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>

      <div v-if="current.photos.length" class="photos">
        <figure v-for="p in current.photos" :key="p.id" :data-photo-id="p.photo_id">
          <img :src="`${BASE}${p.image_url}`" :alt="`照片 ${p.photo_id}`" loading="lazy" />
          <figcaption>{{ p.photo_id }} · {{ p.role }}</figcaption>
        </figure>
      </div>

      <div
        v-for="c in current.candidates"
        :key="c.id"
        class="candidate"
        :class="{ rejected: c.status === 'rejected' }"
        :data-candidate="c.id"
      >
        <label class="check">
          <input
            type="checkbox"
            :checked="selected.has(c.id)"
            :disabled="c.status === 'executed' || c.status === 'rejected'"
            @change="toggle(c)"
          />
          <strong>{{ statusText[c.status] || c.status }}</strong> · v{{ c.version }}
          <span v-if="c.photo_ids.length" class="muted">照片：{{ c.photo_ids.join('、') }}</span>
          <span v-if="c.status === 'rejected'" class="muted">已被重建取代，仅供追溯，不可确认</span>
        </label>

        <div class="fields">
          <label>书名<input data-field="title" v-model="editOf(c).title" type="text" maxlength="500" :disabled="c.status === 'executed' || c.status === 'rejected'" /></label>
          <label>副标题<input data-field="subtitle" v-model="editOf(c).subtitle" type="text" maxlength="500" :disabled="c.status === 'executed' || c.status === 'rejected'" /></label>
          <label>作者（顿号分隔）<input data-field="authors" v-model="editOf(c).authors" type="text" maxlength="200" :disabled="c.status === 'executed' || c.status === 'rejected'" /></label>
          <label>ISBN<input data-field="isbn" v-model="editOf(c).isbn" type="text" maxlength="20" :disabled="c.status === 'executed' || c.status === 'rejected'" /></label>
        </div>
        <div class="cand-actions">
          <button class="btn" :disabled="busy !== '' || c.status === 'executed' || c.status === 'rejected'" @click="saveCandidate(c)">保存（版本 +1）</button>
          <button class="btn" :disabled="busy !== '' || c.status === 'rejected' || c.photo_ids.length < 2" @click="splitCandidate(c)">拆分照片</button>
        </div>

        <p v-if="c.match_book_id" class="match" :class="{ conflict: c.match_diff && Object.keys(c.match_diff).length }">
          命中已有书 #{{ c.match_book_id }}
          <template v-if="c.match_diff && Object.keys(c.match_diff).length">
            ——字段差异：{{ Object.keys(c.match_diff).join('、') }}（确认执行时仅关联，不改已有书字段）
          </template>
        </p>
      </div>

      <div v-if="current.executions.length" class="receipts">
        <h3>执行回执</h3>
        <table>
          <thead>
            <tr><th>#</th><th>状态</th><th>书目</th><th>错误</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="e in current.executions" :key="e.id">
              <td>{{ e.id }}</td>
              <td>{{ statusText[e.status] || e.status }}</td>
              <td>{{ e.book_id ? `#${e.book_id}` : '—' }}</td>
              <td class="err">{{ e.error_code ? `${e.error_code}：${e.error}` : '' }}</td>
              <td>
                <button v-if="e.status === 'failed'" class="btn" :disabled="busy !== ''" @click="retry(e.id)">
                  重试
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
  <section v-else class="batch-intake">
    <p>此页面仅 Owner 可用。</p>
  </section>
</template>

<style scoped>
.batch-intake { max-width: 860px; margin: 0 auto; padding: 12px; }
.muted { color: var(--text-muted); font-size: 0.92rem; }
.toolbar { display: flex; gap: 8px; margin: 12px 0; }
.toolbar input { flex: 1; }
.item-list { list-style: none; padding: 0; }
.item-list li { margin: 4px 0; }
.link { background: none; border: none; color: var(--accent, #1f4e79); cursor: pointer; padding: 4px 2px; font-size: 14px; text-align: left; }
.workbench { margin-top: 16px; display: flex; flex-direction: column; gap: 14px; }
.wb-head h2 { margin: 4px 0 0; font-size: 1.05rem; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.upload { position: relative; overflow: hidden; }
.upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; }
.photos { display: flex; flex-wrap: wrap; gap: 10px; }
.photos figure { margin: 0; text-align: center; }
.photos img { width: 92px; height: 130px; object-fit: cover; border-radius: 6px; border: 1px solid var(--border); }
.photos figcaption { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
.candidate { border: 1px solid var(--border); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px; }
/* BUG-282：软删除（重建取代）候选仅作追溯展示，与当前可操作候选区分 */
.candidate.rejected { opacity: 0.55; background: var(--card-bg); }
.check { display: flex; align-items: center; gap: 8px; }
.fields { display: grid; grid-template-columns: 2fr 2fr 1.5fr 1fr; gap: 8px; }
.fields label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-muted); }
.cand-actions { display: flex; gap: 8px; }
.match { margin: 0; font-size: 13px; }
.match.conflict { color: #a05a00; }
.receipts table { width: 100%; border-collapse: collapse; font-size: 13px; }
.receipts th, .receipts td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--border); }
.err { color: #a03030; font-size: 12px; max-width: 340px; }
.notice { color: #1c6b48; }
input[type='text'], input[type='number'] {
  padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm, 6px);
  font-size: 14px; background: var(--card-bg); color: var(--text); min-height: var(--tap-min, 44px); font-family: inherit;
}
</style>
