<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { sessionRole } from '@/stores/session'
import { copyStatusLabel } from '@/types/models'
import {
  StorageApiError,
  deleteShelfPhoto,
  getShelf,
  listCellCopies,
  newIdempotencyKey,
  shelfPhotoUrl,
  updateShelfPhoto,
  uploadShelfPhoto,
} from '@/stores/storage'
import type { CellCopyItem, ShelfPhoto, StorageShelfDetail } from '@/types/models'

/**
 * 书架详情页（LOC-12）：元数据与统计、照片管理、层格示意图、高亮定位与格子内容列表。
 *
 * 契约要点（M0 冻结 + 修订记录）：
 * - 编号约定「面向书架正面，层从上到下、格子从左到右」固定展示；示意图独立于照片，
 *   不做照片区域圈选（高亮的是示意图格子，对应实际格子 ID）；
 * - 无照片仍可定位；文件缺失显示「照片不可用」不影响其他功能；
 * - 照片上传 multipart 带幂等键；删除期望版本走 query 并二次确认；每架 ≤5 张、单张 ≤10MiB；
 * - 格子副本清单分页；数量口径与后端一致（格子计数=已分配该格的实体副本，含外借等非在架）。
 */

const props = defineProps<{
  shelfId: number
  /** URL query ?cell=<id> 高亮定位（LOC-15 的「查看位置」入口） */
  cell: number | null
}>()

const isOwner = computed(() => sessionRole.value === 'owner')

const loading = ref(true)
const detail = ref<StorageShelfDetail | null>(null)
const errorText = ref('')
const notice = ref('')

// ── 示意图与格子副本清单 ──

const selectedCellId = ref<number | null>(null)
const cellCopies = ref<CellCopyItem[]>([])
const cellCopiesTotal = ref(0)
const copiesOffset = ref(0)
const copiesLoading = ref(false)
const COPIES_PAGE_SIZE = 20
/** 每格已分配副本数（含外借等非在架，§6.3 口径） */
const cellCounts = ref<Record<number, number>>({})

function statusLabelOf(status: string): string {
  return copyStatusLabel(status)
}

const allCellIds = computed(() => {
  if (!detail.value) return []
  return detail.value.layers.flatMap((l) => l.cells.map((c) => c.id))
})

/** 高亮目标格子 ID（props.cell 在模板中被 v-for 变量遮蔽，单独暴露） */
const highlightCellId = computed(() => props.cell)

const gridEl = ref<HTMLElement | null>(null)

async function loadCopies(cellId: number, offset: number) {
  copiesLoading.value = true
  try {
    const page = await listCellCopies(cellId, COPIES_PAGE_SIZE, offset)
    cellCopies.value = page.items
    cellCopiesTotal.value = page.total
    copiesOffset.value = offset
  } catch (e) {
    handleError(e)
  } finally {
    copiesLoading.value = false
  }
}

async function selectCell(cellId: number) {
  selectedCellId.value = cellId
  await loadCopies(cellId, 0)
}

async function load() {
  loading.value = true
  errorText.value = ''
  try {
    detail.value = await getShelf(props.shelfId)
    // 每格副本计数：清单端点无聚合字段，家庭规模下并行取 total（limit=1 只取计数）
    const entries = await Promise.all(
      allCellIds.value.map(async (id) => {
        const page = await listCellCopies(id, 1, 0)
        return [id, page.total] as const
      }),
    )
    cellCounts.value = Object.fromEntries(entries)
    // 高亮定位：query cell 必须对应该书架的实际格子 ID，不凭空高亮
    if (props.cell !== null && allCellIds.value.includes(props.cell)) {
      selectedCellId.value = props.cell
      await loadCopies(props.cell, 0)
      await nextTick()
      gridEl.value
        ?.querySelector(`[data-cell-id="${props.cell}"]`)
        // jsdom 无 scrollIntoView，运行时环境保证存在
        ?.scrollIntoView?.({ block: 'center' })
    }
  } catch (e) {
    handleError(e)
  } finally {
    loading.value = false
  }
}

// ── 照片管理（Owner 写；Member 只读看图） ──

const MAX_PHOTO_BYTES = 10 * 1024 * 1024
const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp']

const uploadFile = ref<File | null>(null)
const uploadCaption = ref('')
const uploadBusy = ref(false)
/** 幂等键随文件选择生成；同一文件重试复用，换文件/成功后重新生成 */
let uploadKey = newIdempotencyKey()

const photoCount = computed(() => detail.value?.photos.length ?? 0)

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  uploadFile.value = input.files?.[0] ?? null
  uploadKey = newIdempotencyKey()
}

async function submitUpload() {
  const file = uploadFile.value
  if (!file || uploadBusy.value) return
  errorText.value = ''
  if (!PHOTO_TYPES.includes(file.type)) {
    errorText.value = '仅支持 JPEG、PNG、WebP 图片'
    return
  }
  if (file.size > MAX_PHOTO_BYTES) {
    errorText.value = '单张照片不能超过 10 MiB'
    return
  }
  uploadBusy.value = true
  try {
    await uploadShelfPhoto(props.shelfId, file, {
      caption: uploadCaption.value.trim() || undefined,
      idempotency_key: uploadKey,
    })
    notice.value = '照片已上传'
    uploadFile.value = null
    uploadCaption.value = ''
    uploadKey = newIdempotencyKey()
    await load()
  } catch (e) {
    handleError(e)
  } finally {
    uploadBusy.value = false
  }
}

async function setPrimary(photo: ShelfPhoto) {
  if (photo.is_primary || uploadBusy.value) return
  uploadBusy.value = true
  errorText.value = ''
  try {
    await updateShelfPhoto(photo.id, {
      version: photo.version,
      is_primary: true,
      idempotency_key: newIdempotencyKey(),
    })
    notice.value = '已设为主图'
    await load()
  } catch (e) {
    handleError(e)
  } finally {
    uploadBusy.value = false
  }
}

async function removePhoto(photo: ShelfPhoto) {
  if (uploadBusy.value) return
  if (!window.confirm('确定删除这张照片？删除后不可恢复。')) return
  uploadBusy.value = true
  errorText.value = ''
  try {
    await deleteShelfPhoto(photo.id, photo.version, newIdempotencyKey())
    notice.value = '照片已删除'
    await load()
  } catch (e) {
    handleError(e)
  } finally {
    uploadBusy.value = false
  }
}

function onPhotoImgError(e: Event) {
  // §8：文件缺失时显示「照片不可用」，其他功能继续可用
  const img = e.target as HTMLImageElement
  img.style.display = 'none'
  img.parentElement?.querySelector('.photo-unavailable')?.removeAttribute('hidden')
}

// ── 错误处理 ──

function handleError(e: unknown) {
  if (e instanceof StorageApiError && e.code === 'PLACEMENT_CHANGED') {
    // 版本冲突：刷新最新数据后提示（load 起始会清空 errorText，消息在刷新完成后写入）
    load().finally(() => {
      errorText.value = '数据已被其他操作修改，已为你刷新最新数据，请核对后重试。'
    })
    return
  }
  errorText.value = e instanceof Error ? e.message : '操作失败'
}

onMounted(load)
</script>

<template>
  <section class="shelf-detail">
    <p class="back"><RouterLink to="/storage">← 返回实体书架清单</RouterLink></p>

    <div v-if="loading" class="muted">加载中…</div>
    <template v-else-if="detail">
      <header class="shelf-head">
        <h1>
          {{ detail.name }}
          <span class="code">{{ detail.code }}</span>
          <span v-if="detail.archived_at" class="badge archived">已归档</span>
        </h1>
        <p v-if="detail.position_note" class="muted">{{ detail.position_note }}</p>
        <p class="stats">
          已分配位置 {{ detail.stats.assigned_copies }} 册 ·
          在位 {{ detail.stats.present_copies }} 册 ·
          在架 {{ detail.stats.on_shelf_copies }} 册 ·
          涉及书目 {{ detail.stats.books_involved }} 本
        </p>
      </header>

      <!-- 照片区：Member 只读看图；Owner 上传/设主图/删除 -->
      <section class="photos" aria-label="书架外观照片">
        <h2>外观照片</h2>
        <p v-if="detail.photos.length === 0" class="muted" data-testid="no-photos">
          暂无外观照片；不影响定位——下方层格示意图始终可用。
        </p>
        <ul v-else class="photo-list">
          <li v-for="photo in detail.photos" :key="photo.id" :data-photo-id="photo.id">
            <div class="photo-frame">
              <img
                :src="shelfPhotoUrl(photo.id)"
                :alt="photo.caption || `${detail.name} 外观照片`"
                loading="lazy"
                @error="onPhotoImgError"
              />
              <span class="photo-unavailable" hidden>照片不可用</span>
            </div>
            <p class="photo-meta">
              <span v-if="photo.is_primary" class="badge primary">主图</span>
              <span v-if="photo.caption" class="caption">{{ photo.caption }}</span>
            </p>
            <div v-if="isOwner" class="mini-actions">
              <button
                type="button"
                :disabled="uploadBusy || photo.is_primary"
                @click="setPrimary(photo)"
              >设为主图</button>
              <button type="button" :disabled="uploadBusy" @click="removePhoto(photo)">删除</button>
            </div>
          </li>
        </ul>
        <form v-if="isOwner && !detail.archived_at" class="upload-form" @submit.prevent="submitUpload">
          <label>
            上传照片（JPEG/PNG/WebP，单张 ≤10 MiB，每架最多 5 张）
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              data-field="photo-file"
              @change="onFileChange"
            />
          </label>
          <label>
            说明（可选）
            <input v-model="uploadCaption" type="text" maxlength="200" data-field="photo-caption" />
          </label>
          <button type="submit" class="btn primary" :disabled="uploadBusy || !uploadFile || photoCount >= 5">
            上传
          </button>
        </form>
      </section>

      <!-- 层格示意图：独立于照片，高亮对应实际格子 ID -->
      <section class="grid-section" aria-label="层格示意图">
        <h2>层格示意图</h2>
        <p class="muted">编号约定：面向书架正面，层从上到下、格子从左到右。格子计数为已分配该格的实体副本（含外借等非在架）。</p>
        <div ref="gridEl" class="grid" role="group" aria-label="书架层格">
          <div v-for="(layer, li) in detail.layers" :key="layer.id" class="grid-layer" :data-layer-id="layer.id">
            <span class="grid-layer-label">{{ layer.label || `第 ${li + 1} 层` }}</span>
            <div class="grid-cells">
              <button
                v-for="cell in layer.cells"
                :key="cell.id"
                type="button"
                class="grid-cell"
                :class="{ selected: selectedCellId === cell.id, highlighted: cell.id === highlightCellId }"
                :data-cell-id="cell.id"
                :aria-pressed="selectedCellId === cell.id"
                @click="selectCell(cell.id)"
              >
                <span class="grid-cell-code">{{ cell.code }}</span>
                <span class="grid-cell-label">{{ cell.label }}</span>
                <span class="grid-cell-count">{{ cellCounts[cell.id] ?? 0 }} 册</span>
              </button>
            </div>
          </div>
        </div>
      </section>

      <!-- 格子内容列表 -->
      <section v-if="selectedCellId !== null" class="cell-copies" aria-label="格子内容">
        <h2>格子内容</h2>
        <div v-if="copiesLoading" class="muted">加载中…</div>
        <template v-else>
          <p v-if="cellCopies.length === 0" class="muted">该格子暂无登记的副本。</p>
          <ul v-else class="copy-list">
            <li v-for="copy in cellCopies" :key="copy.copy_id" :data-copy-id="copy.copy_id">
              <RouterLink :to="`/books/${copy.book_id}`" class="copy-title">{{ copy.book_title }}</RouterLink>
              <span class="muted">副本 #{{ copy.copy_id }}</span>
              <span>{{ statusLabelOf(copy.status) }}</span>
              <span v-if="copy.owner_member_name" class="muted">归属 {{ copy.owner_member_name }}</span>
            </li>
          </ul>
          <div v-if="cellCopiesTotal > COPIES_PAGE_SIZE" class="pager">
            <button type="button" class="btn" :disabled="copiesOffset === 0" @click="loadCopies(selectedCellId!, copiesOffset - COPIES_PAGE_SIZE)">上一页</button>
            <span class="muted">{{ copiesOffset + 1 }}–{{ Math.min(copiesOffset + COPIES_PAGE_SIZE, cellCopiesTotal) }} / {{ cellCopiesTotal }}</span>
            <button type="button" class="btn" :disabled="copiesOffset + COPIES_PAGE_SIZE >= cellCopiesTotal" @click="loadCopies(selectedCellId!, copiesOffset + COPIES_PAGE_SIZE)">下一页</button>
          </div>
        </template>
      </section>

      <p v-if="errorText" class="error-box" role="alert">{{ errorText }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    </template>
    <template v-else>
      <p class="error-box" role="alert">{{ errorText || '书架不存在或不可见。' }}</p>
    </template>
  </section>
</template>

<style scoped>
.shelf-detail { max-width: 960px; margin: 0 auto; padding: var(--space-3, 12px); }
.back { margin: 0 0 8px; }
.muted { color: var(--text-muted, #5a6878); font-size: 0.92rem; }
.shelf-head h1 { font-size: 1.3rem; margin: 0 0 4px; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.code { font-size: 0.85rem; color: var(--text-muted, #5a6878); font-weight: 400; }
.badge.archived {
  font-size: 0.78rem; color: #8a5a19; background: #fdf0dc;
  border: 1px solid #ecd9b8; border-radius: 999px; padding: 1px 8px;
}
.badge.primary {
  font-size: 0.78rem; color: #1c6b48; background: #e2f4ea;
  border: 1px solid #bfe6d0; border-radius: 999px; padding: 1px 8px;
}
.stats { font-size: 0.9rem; margin: 4px 0 0; }
h2 { font-size: 1.05rem; margin: 20px 0 8px; }
.photo-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
.photo-frame { aspect-ratio: 4 / 3; background: var(--bg-muted, #f1f4f8); border-radius: 8px; overflow: hidden; display: flex; align-items: center; justify-content: center; }
.photo-frame img { width: 100%; height: 100%; object-fit: cover; display: block; }
.photo-unavailable { color: var(--text-muted, #5a6878); font-size: 0.88rem; }
.photo-meta { margin: 4px 0; display: flex; gap: 6px; align-items: center; font-size: 0.88rem; }
.mini-actions { display: flex; gap: 6px; }
.mini-actions button {
  padding: 4px 8px; font-size: 0.82rem; border-radius: 6px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.mini-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.upload-form {
  margin-top: 12px; display: flex; flex-wrap: wrap; gap: 10px; align-items: end;
  border-top: 1px dashed var(--border, #d7dee8); padding-top: 12px;
}
.upload-form label { display: grid; gap: 4px; font-size: 0.88rem; }
.upload-form input[type='text'] { padding: 6px 10px; border: 1px solid var(--border, #d7dee8); border-radius: 8px; }
.btn {
  padding: 6px 12px; border-radius: 8px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.grid { display: grid; gap: 10px; }
.grid-layer { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.grid-layer-label { min-width: 64px; font-size: 0.9rem; color: var(--text-muted, #5a6878); }
.grid-cells { display: flex; gap: 8px; flex-wrap: wrap; flex: 1; }
.grid-cell {
  display: grid; gap: 2px; justify-items: center;
  padding: 8px 10px; min-width: 76px;
  background: var(--card-bg, #fff); border: 1px solid var(--border, #d7dee8); border-radius: 8px;
  cursor: pointer; font-size: 0.85rem; color: inherit;
}
.grid-cell:hover { border-color: var(--primary, #2563eb); }
.grid-cell.selected { border-color: var(--primary, #2563eb); box-shadow: 0 0 0 1px var(--primary, #2563eb); }
.grid-cell.highlighted { outline: 2px solid #e8a33d; outline-offset: 1px; }
.grid-cell-code { font-weight: 600; font-variant-numeric: tabular-nums; }
.grid-cell-label { color: var(--text-muted, #5a6878); }
.grid-cell-count { font-size: 0.8rem; color: var(--text-muted, #5a6878); }
.copy-list { list-style: none; padding: 0; margin: 0; background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0); border-radius: 10px; }
.copy-list li { display: flex; flex-wrap: wrap; gap: 10px; padding: 8px 14px; border-bottom: 1px solid var(--border, #eef2f6); font-size: 0.92rem; align-items: baseline; }
.copy-list li:last-child { border-bottom: none; }
.copy-title { font-weight: 500; }
.pager { display: flex; gap: 10px; align-items: center; margin-top: 8px; }
.error-box {
  background: #fdecea; border: 1px solid #f5c6c0; border-radius: 8px;
  padding: 10px 12px; color: #b3261e; font-size: 0.92rem;
}
.notice { color: #1c6b48; }
@media (max-width: 600px) {
  .grid-layer { flex-direction: column; align-items: stretch; }
  .grid-cells { flex-direction: column; }
  .grid-cell { width: 100%; }
}
</style>
