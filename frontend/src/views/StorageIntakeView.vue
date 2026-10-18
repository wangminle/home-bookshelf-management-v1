<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { useMembersStore } from '@/stores/members'
import { copyStatusLabel } from '@/types/models'
import type { BookDetail } from '@/types/models'
import {
  getStorageBook, listUnlocated, pendingLocationOperations, prepareLocationOperation,
  previewPlacement, sendLocationOperation, StorageApiError,
} from '@/stores/storage'
import type { IntakeCommitPayload, MovePreview, PlacementTargetInput, UnlocatedPage } from '@/stores/storage'
import PlacementTargetPicker from '@/components/PlacementTargetPicker.vue'
import LocationOperationRecovery from '@/components/LocationOperationRecovery.vue'

const props = withDefaults(defineProps<{ bookIds?: number[] }>(), { bookIds: () => [] })
const members = useMembersStore()
const isOwner = computed(() => sessionRole.value === 'owner')
const tabs = [
  { key: 'copies_without_placement', label: '有副本未定位' },
  { key: 'legacy_location_only', label: '仅有旧位置文字' },
  { key: 'books_without_physical_copy', label: '尚无实体副本' },
] as const

type Bucket = typeof tabs[number]['key']
interface Draft { book: BookDetail; count: number; ownerId: number | '' }
const bucket = ref<Bucket>('copies_without_placement')
const page = ref<UnlocatedPage | null>(null)
const offsets = ref<Record<Bucket, number>>({ copies_without_placement: 0, legacy_location_only: 0, books_without_physical_copy: 0 })
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const selectedCopies = ref<number[]>([])
const selectedBooks = ref<number[]>([])
const drafts = ref<Draft[]>([])
const target = ref<PlacementTargetInput | null>(null)
const targetDisplay = ref('')
const movePreview = ref<MovePreview | null>(null)
const provisionPreview = ref<IntakeCommitPayload['items'] | null>(null)
const pending = ref(false)
const recovery = ref<InstanceType<typeof LocationOperationRecovery> | null>(null)
const locked = computed(() => busy.value || pending.value)
const total = computed(() => page.value?.[bucket.value].total ?? 0)
const copyRows = computed(() => bucket.value === 'books_without_physical_copy' ? [] : page.value?.[bucket.value].items ?? [])
const bookRows = computed(() => page.value?.books_without_physical_copy.items ?? [])
let listGeneration = 0
let bookGeneration = 0
let previewGeneration = 0

function syncPending() {
  pending.value = false
  if (!isOwner.value) return
  try { pending.value = pendingLocationOperations().length > 0 }
  catch (e) { pending.value = true; error.value = message(e) }
  recovery.value?.refresh()
}
function message(e: unknown): string { return e instanceof Error ? e.message : '操作失败，请重试' }

async function loadList() {
  const generation = ++listGeneration
  const key = bucket.value
  loading.value = true
  try {
    const value = await listUnlocated(20, offsets.value[key])
    if (generation !== listGeneration) return
    page.value = value
  } catch (e) { if (generation === listGeneration) { error.value = message(e); throw e } }
  finally { if (generation === listGeneration) loading.value = false }
}
function reloadList() { error.value = ''; void loadList().catch(() => {}) }
function changeBucket(key: Bucket) { bucket.value = key; reloadList() }
function paginate(direction: number) { offsets.value[bucket.value] = Math.max(0, offsets.value[bucket.value] + direction * 20); reloadList() }
function toggleCopy(id: number, checked: boolean) {
  selectedCopies.value = checked ? [...new Set([...selectedCopies.value, id])] : selectedCopies.value.filter((v) => v !== id)
}
function toggleBook(id: number, checked: boolean) {
  selectedBooks.value = checked ? [...new Set([...selectedBooks.value, id])] : selectedBooks.value.filter((v) => v !== id)
}

async function loadBooks(ids: number[]) {
  const generation = ++bookGeneration
  if (ids.length > 100 || ids.some((id) => !Number.isSafeInteger(id) || id < 1)) {
    error.value = '书目参数无效，每次最多核对100个书目。'; drafts.value = []; return
  }
  const unique = [...new Set(ids)]
  busy.value = true
  try {
    const books = await Promise.all(unique.map(getStorageBook))
    if (generation !== bookGeneration) return
    drafts.value = books.map((book) => ({ book, count: 0, ownerId: '' }))
    selectedBooks.value = unique
  } catch (e) { if (generation === bookGeneration) { drafts.value = []; error.value = `读取已有副本失败：${message(e)}` } }
  finally { if (generation === bookGeneration) busy.value = false }
}

const totalNew = computed(() => drafts.value.reduce((sum, d) => sum + Number(d.count || 0), 0))
const canProvision = computed(() => !!target.value && totalNew.value > 0 && totalNew.value <= 100 &&
  drafts.value.every((d) => Number.isInteger(d.count) && d.count >= 0 && d.count <= 100 && (d.count === 0 || d.ownerId !== '')))

watch(() => JSON.stringify([selectedCopies.value, target.value]), () => { ++previewGeneration; movePreview.value = null })
watch(() => JSON.stringify([drafts.value.map((d) => [d.book.id, d.count, d.ownerId]), target.value]), () => { provisionPreview.value = null })

async function previewMove() {
  if (locked.value || !target.value || !selectedCopies.value.length || selectedCopies.value.length > 100) return
  const generation = ++previewGeneration
  error.value = ''; busy.value = true
  try {
    const value = await previewPlacement(target.value, [...selectedCopies.value].sort((a, b) => a - b))
    if (generation === previewGeneration) movePreview.value = value
  } catch (e) { error.value = message(e) }
  finally { busy.value = false }
}
function previewProvision() {
  if (locked.value || !canProvision.value || !target.value) return
  provisionPreview.value = drafts.value.filter((d) => d.count > 0).sort((a, b) => a.book.id - b.book.id).map((d) => ({
    book_id: d.book.id,
    new_copies: { count: d.count, owner_member_id: Number(d.ownerId), target: { ...target.value! } },
  }))
}

async function finish(result: unknown) {
  const value = result as { created?: number; moved?: number }
  notice.value = value.created !== undefined ? `已补录 ${value.created} 册实体副本` : `已移动 ${value.moved ?? 0} 册，未新增副本`
  movePreview.value = null; provisionPreview.value = null
  selectedCopies.value = []; selectedBooks.value = []; drafts.value = []
  syncPending()
  try { await loadList() }
  catch { error.value = '操作已完成，但清单刷新失败。请重试读取清单，不要再次提交。' }
}

async function commit(kind: 'move' | 'provision') {
  if (locked.value) return
  let payload: Record<string, unknown>
  let label: string
  if (kind === 'move') {
    const preview = movePreview.value
    if (!preview) return
    const { shelf_id, cell_id, shelf_version, cell_version } = preview.target
    payload = { target: { shelf_id, cell_id, shelf_version, cell_version }, preview_digest: preview.preview_digest,
      copies: preview.copies.map((c) => ({ copy_id: c.copy_id, placement_version: c.placement_version })) }
    label = `移动${preview.copies.length}册至${preview.target.display}`
  } else {
    if (!provisionPreview.value) return
    payload = { items: provisionPreview.value }
    label = `补录${totalNew.value}册：${drafts.value.filter((d) => d.count > 0).map((d) => `《${d.book.title}》${d.count}册（${members.members.find((m) => m.id === Number(d.ownerId))?.name ?? '所选成员'}）`).join('、')} → ${targetDisplay.value || '所选书架/格子'}`
  }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const op = prepareLocationOperation(kind, payload, label)
    const result = await sendLocationOperation(op)
    await finish(result)
  } catch (e) {
    error.value = message(e)
    if (e instanceof StorageApiError && e.status >= 400 && e.status < 500 && ![401, 403, 429].includes(e.status)) {
      movePreview.value = null; provisionPreview.value = null
      // 拒绝并未提交；必须重新读取和确认，不能偷偷重试旧预览。
      try { await loadList() } catch { error.value += '；清单刷新失败，请先重新加载。' }
    }
  } finally { busy.value = false; syncPending() }
}

watch([() => props.bookIds, sessionRole, sessionMemberId], () => {
  ++bookGeneration; ++previewGeneration
  selectedCopies.value = []; selectedBooks.value = []; drafts.value = []
  movePreview.value = null; provisionPreview.value = null
  error.value = ''; notice.value = ''; busy.value = false
  syncPending(); reloadList()
  if (props.bookIds.length) void loadBooks(props.bookIds)
  if (isOwner.value && !members.loaded) void members.load().catch((e) => { error.value = `成员加载失败：${message(e)}` })
}, { immediate: true })
</script>

<template>
  <section class="intake-page">
    <header class="page-heading">
      <div><p class="eyebrow">实体藏书整理</p><h1>副本与位置登记</h1></div>
      <RouterLink to="/storage">返回实体书架</RouterLink>
    </header>
    <p class="intro">先核对家中实际册数。已有副本只分配位置；照片张数与书目数都不代表实体册数。</p>
    <p v-if="!isOwner" class="hint">当前为只读浏览，副本补录与位置移动由 Owner 操作。</p>
    <LocationOperationRecovery ref="recovery" @completed="finish" @changed="syncPending" />
    <p v-if="notice" role="status" class="success">{{ notice }}</p>
    <div v-if="error" role="alert" class="error-box">{{ error }} <button type="button" @click="reloadList">重新加载清单</button></div>

    <section class="catalogue" aria-label="未定位分类清单">
      <div class="category-tabs" role="tablist" aria-label="未定位分类">
        <button v-for="tab in tabs" :key="tab.key" :data-tab="tab.key" type="button" role="tab"
          :aria-selected="bucket === tab.key" :class="{ active: bucket === tab.key }" :disabled="busy" @click="changeBucket(tab.key)">
          {{ tab.label }} <span>{{ page?.[tab.key].total ?? '—' }}</span>
        </button>
      </div>
      <div v-if="loading" class="loading" role="status">正在读取清单…</div>
      <template v-else-if="page">
        <ul class="unlocated-list" v-if="bucket !== 'books_without_physical_copy'">
          <li v-for="copy in copyRows" :key="copy.copy_id">
            <label><input v-if="isOwner" :data-copy="copy.copy_id" type="checkbox" :disabled="locked" :checked="selectedCopies.includes(copy.copy_id)" @change="toggleCopy(copy.copy_id, ($event.target as HTMLInputElement).checked)" />
              <span><RouterLink :to="`/books/${copy.book_id}#copies`">{{ copy.book_title }}</RouterLink><small>副本 #{{ copy.copy_id }} · {{ copyStatusLabel(copy.status) }} · {{ copy.owner_member_name || '归属未登记' }}</small>
                <small>{{ copy.location ? `旧位置文字：${copy.location}` : '位置未登记' }}</small></span></label>
          </li>
        </ul>
        <ul v-else class="unlocated-list">
          <li v-for="book in bookRows" :key="book.book_id"><label>
            <input v-if="isOwner" :data-book="book.book_id" type="checkbox" :disabled="locked" :checked="selectedBooks.includes(book.book_id)" @change="toggleBook(book.book_id, ($event.target as HTMLInputElement).checked)" />
            <span><RouterLink :to="`/books/${book.book_id}#copies`">{{ book.book_title }}</RouterLink><small>尚无实体副本 · 电子副本 {{ book.digital_copies }} 册（不计实体册数）</small></span>
          </label></li>
        </ul>
        <p v-if="total === 0" class="hint">此分类暂无待登记项目；系统记录不代表现场实际为空。</p>
        <div class="pagination">
          <button type="button" data-action="prev" :disabled="!offsets[bucket] || busy" @click="paginate(-1)">上一页</button>
          <span>{{ Math.floor(offsets[bucket] / 20) + 1 }} / {{ Math.max(1, Math.ceil(total / 20)) }} 页 · {{ total }} 项</span>
          <button type="button" data-action="next" :disabled="offsets[bucket] + 20 >= total || busy" @click="paginate(1)">下一页</button>
        </div>
        <button v-if="isOwner && bucket === 'books_without_physical_copy'" type="button" :disabled="locked || !selectedBooks.length || selectedBooks.length > 100" @click="loadBooks(selectedBooks)">核对选中书目的已有副本与册数</button>
      </template>
    </section>

    <section v-if="isOwner" class="registration" aria-label="核对与登记">
      <h2>核对与登记</h2>
      <p v-if="pending" class="hint">有操作结果待核对，请先使用上方恢复入口，暂不开始新的登记。</p>
      <fieldset :disabled="locked">
        <legend>本批目标位置</legend>
        <PlacementTargetPicker @update:target="target = $event" @update:display="targetDisplay = $event" />
        <p class="hint">本批统一登记到所选书架或格子；不同位置可分批操作。</p>
        <div v-if="selectedCopies.length" class="selected-summary">已选择 {{ selectedCopies.length }} 册已有副本；只移动位置，不新增册数。</div>
        <button type="button" data-action="preview-move" :disabled="!target || !selectedCopies.length || selectedCopies.length > 100" @click="previewMove">预览移动 {{ selectedCopies.length }} 册</button>
        <p v-if="selectedCopies.length > 100" role="alert">每批最多100册，请减少选择。</p>
        <div v-for="draft in drafts" :key="draft.book.id" class="book-draft">
          <h3>{{ draft.book.title }}</h3>
          <p>已有实体副本 {{ draft.book.copies.filter(c => c.copy_type === 'physical').length }} 册</p>
          <label v-for="copy in draft.book.copies.filter(c => c.copy_type === 'physical')" :key="copy.id" class="existing-copy">
            <input type="checkbox" :data-existing-copy="copy.id" :checked="selectedCopies.includes(copy.id)" @change="toggleCopy(copy.id, ($event.target as HTMLInputElement).checked)" />
            只移动已有副本 #{{ copy.id }} · {{ copyStatusLabel(copy.status) }} · {{ copy.location_display || copy.location || '未定位' }}
          </label>
          <div class="new-copy-fields">
            <label>实际新增实体册数 <input :data-count="draft.book.id" v-model.number="draft.count" type="number" min="0" max="100" step="1" /></label>
            <label>新增副本归属 <select :data-owner="draft.book.id" v-model="draft.ownerId"><option value="">请选择成员</option><option v-for="member in members.members" :key="member.id" :value="member.id">{{ member.name }}</option></select></label>
          </div>
          <p class="hint">新增量为0时不创建副本；请勿把已有册数再填作新增量。</p>
        </div>
        <button v-if="drafts.length" type="button" data-action="preview-provision" :disabled="!canProvision" @click="previewProvision">核对新增 {{ totalNew }} 册</button>
      </fieldset>
      <section v-if="movePreview" class="review-panel" aria-label="移动预览">
        <h3>确认移动 {{ movePreview.copies.length }} 册</h3>
        <ul><li v-for="copy in movePreview.copies" :key="copy.copy_id">《{{ copy.book_title }}》副本 #{{ copy.copy_id }}：{{ copy.from.display || '未定位' }} → {{ copy.to.display }}</li></ul>
        <button type="button" class="primary" data-action="confirm-move" :disabled="locked" @click="commit('move')">确认移动（不新增副本）</button>
      </section>
      <section v-if="provisionPreview" class="review-panel" aria-label="补录预览">
        <h3>确认本次新增 {{ totalNew }} 册</h3>
        <ul><li v-for="item in provisionPreview" :key="item.book_id">《{{ drafts.find(d => d.book.id === item.book_id)?.book.title }}》：已有 {{ drafts.find(d => d.book.id === item.book_id)?.book.copies.filter(c => c.copy_type === 'physical').length }} 册，新增 {{ item.new_copies?.count }} 册；归属 {{ members.members.find(m => m.id === item.new_copies?.owner_member_id)?.name }}；位置 {{ targetDisplay || '所选书架/格子' }}</li></ul>
        <p>本次仅新增已确认的册数，不修改阅读进度、笔记或购买记录。</p>
        <button type="button" class="primary" data-action="confirm-provision" :disabled="locked" @click="commit('provision')">确认补录</button>
      </section>
    </section>
  </section>
</template>

<style scoped>
.intake-page { max-width: 960px; margin: 0 auto; padding: 12px 0 40px; }
.page-heading { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.eyebrow { color: var(--text-muted); font-size: .8rem; letter-spacing: .12em; margin: 0; }
h1 { margin: 6px 0 12px; font-size: 1.6rem; } h2 { font-size: 1.15rem; } h3 { font-size: 1rem; }
a { color: var(--primary); } .intro, .hint { color: var(--text-muted); line-height: 1.65; }
.catalogue, .registration { background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-top: 20px; }
.category-tabs { display: flex; flex-wrap: wrap; gap: 8px; border-bottom: 1px solid var(--border); padding-bottom: 12px; }
.category-tabs .active, .primary { background: var(--primary); color: white; }
button { min-height: 44px; border: 1px solid var(--border); background: var(--card-bg); color: var(--text); border-radius: 6px; padding: 8px 14px; cursor: pointer; }
button:disabled { opacity: .5; cursor: not-allowed; }
.unlocated-list { list-style: none; padding: 0; }
.unlocated-list li { padding: 12px 0; border-bottom: 1px solid var(--border); }
.unlocated-list label { display: flex; align-items: center; gap: 12px; min-height: 44px; }
small { display: block; color: var(--text-muted); margin-top: 6px; }
input[type=checkbox] { width: 20px; height: 20px; flex-shrink: 0; }
.pagination { display: flex; gap: 16px; align-items: center; justify-content: space-between; margin: 16px 0; }
fieldset { border: none; margin: 0; padding: 0; display: grid; gap: 14px; } legend { padding: 8px 0; font-weight: 600; }
.book-draft, .review-panel { border-top: 1px solid var(--border); padding-top: 16px; margin-top: 16px; }
.new-copy-fields { display: flex; flex-wrap: wrap; gap: 16px; }
.new-copy-fields label { display: grid; gap: 6px; }
.new-copy-fields input, select { min-height: 44px; box-sizing: border-box; background: var(--card-bg); color: var(--text); border: 1px solid var(--border); border-radius: 6px; padding: 8px; }
.existing-copy { display: flex; align-items: center; gap: 8px; min-height: 44px; }
.error-box { background: var(--card-bg); padding: 12px; color: var(--error, #b3261e); border-left: 3px solid currentColor; }
.success { color: var(--primary); padding: 12px; border: 1px solid var(--border); }
.review-panel li { padding: 6px 0; line-height: 1.6; }
@media(max-width: 600px) { .catalogue, .registration { padding: 14px; } .page-heading { align-items: flex-start; } .new-copy-fields { display: grid; } .category-tabs button { width: 100%; text-align: left; } .pagination { gap: 8px; font-size: .85rem; } }
</style>
