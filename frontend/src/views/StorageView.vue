<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { sessionRole } from '@/stores/session'
import { lastError } from '@/stores/api'
import {
  StorageApiError,
  createRoom,
  createShelf,
  getShelf,
  listRooms,
  listShelves,
  newIdempotencyKey,
  shelfPhotoUrl,
  updateRoom,
  updateShelf,
} from '@/stores/storage'
import StorageRoomForm from '@/components/StorageRoomForm.vue'
import StorageShelfForm from '@/components/StorageShelfForm.vue'
import ShelfLayoutEditor from '@/components/ShelfLayoutEditor.vue'
import type {
  LocationStats,
  StorageRoomWithStats,
  StorageShelf,
  StorageShelfDetail,
} from '@/types/models'

/**
 * 实体书架清单页（LOC-10）：房间分组 + 书架卡片 + 建档/编辑 + 归档状态 + 空态。
 * 权限：登录成员可读（后端 locations:read），管理按钮仅 Owner 显示（后端同样拒绝非 Owner）。
 * 与首页「书架」封面墙区分：本页管理实体房间/书架，封面墙继续承担藏书浏览。
 */

const loading = ref(true)
const busy = ref(false)
const notice = ref('')
const rooms = ref<StorageRoomWithStats[]>([])
const shelvesByRoom = ref<Record<number, StorageShelf[]>>({})
const shelfDetails = ref<Record<number, StorageShelfDetail>>({})
const includeArchived = ref(false)
const keyword = ref('')

// 表单状态：null=关闭；{ room: null }=新建房间；{ room }=编辑房间
const roomForm = ref<{ room: StorageRoomWithStats | null } | null>(null)
const shelfForm = ref<{ shelf: StorageShelf | null; roomId: number; roomName: string } | null>(null)
// LOC-11：布局编辑器（Owner，仅未归档房间下的未归档书架）
const layoutShelfId = ref<number | null>(null)

const isOwner = computed(() => sessionRole.value === 'owner')

/** 关键字过滤：命中房间名/编号则整组保留，否则按书架名/编号过滤 */
const visibleRooms = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return rooms.value
    .map((room) => {
      const shelves = shelvesByRoom.value[room.id] ?? []
      if (!kw) return { room, shelves }
      const roomHit = room.name.toLowerCase().includes(kw) || room.code.toLowerCase().includes(kw)
      if (roomHit) return { room, shelves }
      return {
        room,
        shelves: shelves.filter(
          (s) => s.name.toLowerCase().includes(kw) || s.code.toLowerCase().includes(kw),
        ),
      }
    })
    .filter((entry) => !kw || entry.shelves.length > 0 ||
      entry.room.name.toLowerCase().includes(kw) || entry.room.code.toLowerCase().includes(kw))
})

function statsOf(room: StorageRoomWithStats): LocationStats {
  return room.stats ?? { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 }
}

function shelfStats(shelfId: number): LocationStats | null {
  return shelfDetails.value[shelfId]?.stats ?? null
}

function layerCountOf(shelfId: number): number {
  return shelfDetails.value[shelfId]?.layers.length ?? 0
}

function cellCountOf(shelfId: number): number {
  const detail = shelfDetails.value[shelfId]
  if (!detail) return 0
  return detail.layers.reduce((sum, layer) => sum + layer.cells.length, 0)
}

function primaryPhotoUrl(shelfId: number): string | null {
  const photos = shelfDetails.value[shelfId]?.photos ?? []
  const primary = photos.find((p) => p.is_primary) ?? photos[0]
  return primary ? shelfPhotoUrl(primary.id) : null
}

async function loadAll() {
  loading.value = true
  try {
    const roomPage = await listRooms(includeArchived.value)
    rooms.value = roomPage.items
    const shelfEntries = await Promise.all(
      roomPage.items.map(async (room) => {
        const page = await listShelves(room.id, includeArchived.value)
        return [room.id, page.items] as const
      }),
    )
    shelvesByRoom.value = Object.fromEntries(shelfEntries)
    // 书架卡片需要统计/层格数/主图：清单端点不含这些字段（契约不新增路由，
    // 统计并入详情端点），家庭规模下逐个取详情可接受。
    const allShelves = shelfEntries.flatMap(([, shelves]) => shelves)
    const detailEntries = await Promise.all(
      allShelves.map(async (shelf) => [shelf.id, await getShelf(shelf.id)] as const),
    )
    shelfDetails.value = Object.fromEntries(detailEntries)
  } catch (e) {
    handleError(e)
  } finally {
    loading.value = false
  }
}

function handleError(e: unknown) {
  if (e instanceof StorageApiError && e.code === 'PLACEMENT_CHANGED') {
    // 409 期望版本失效：提示刷新并自动重载最新数据（§7.3 版本冲突不自动重试写操作）
    lastError.value = '数据已被其他操作修改，已为你刷新最新数据，请核对后重试'
    loadAll()
    return
  }
  lastError.value = e instanceof Error ? e.message : '操作失败'
}

function toggleArchivedFilter() {
  includeArchived.value = !includeArchived.value
  loadAll()
}

function onLayoutSaved() {
  // 布局变化影响层格数与统计口径，重载清单后关闭编辑器
  layoutShelfId.value = null
  loadAll()
}

// ── 房间建档/编辑 ──

async function submitRoomForm(payload: { code: string; name: string; description: string | null; sort_order: number }) {
  if (!roomForm.value) return
  busy.value = true
  try {
    if (roomForm.value.room) {
      const room = roomForm.value.room
      await updateRoom(room.id, { version: room.version, ...payload, idempotency_key: newIdempotencyKey() })
      notice.value = `房间「${payload.name}」已保存`
    } else {
      await createRoom({ ...payload, idempotency_key: newIdempotencyKey() })
      notice.value = `房间「${payload.name}」已创建`
    }
    roomForm.value = null
    await loadAll()
  } catch (e) {
    handleError(e)
  } finally {
    busy.value = false
  }
}

// ── 书架建档/编辑 ──

async function submitShelfForm(payload: {
  code: string
  name: string
  position_note: string | null
  sort_order: number
  layers?: { label?: string; cells?: { label?: string }[] }[]
}) {
  if (!shelfForm.value) return
  busy.value = true
  try {
    if (shelfForm.value.shelf) {
      const shelf = shelfForm.value.shelf
      const { layers: _ignored, ...edits } = payload
      await updateShelf(shelf.id, { version: shelf.version, ...edits, idempotency_key: newIdempotencyKey() })
      notice.value = `书架「${payload.name}」已保存`
    } else {
      await createShelf({
        ...payload,
        room_id: shelfForm.value.roomId,
        idempotency_key: newIdempotencyKey(),
      })
      notice.value = `书架「${payload.name}」已创建`
    }
    shelfForm.value = null
    await loadAll()
  } catch (e) {
    handleError(e)
  } finally {
    busy.value = false
  }
}

// ── 归档/恢复（二次确认；有副本引用时后端返回 409 LOCATION_IN_USE） ──

async function toggleRoomArchived(room: StorageRoomWithStats) {
  const archiving = room.archived_at === null
  const message = archiving
    ? `确定归档房间「${room.name}」？归档后其中书架与位置只读，仍被副本引用时会被拒绝。`
    : `确定恢复房间「${room.name}」？`
  if (!window.confirm(message)) return
  busy.value = true
  try {
    await updateRoom(room.id, {
      version: room.version,
      archived: archiving,
      idempotency_key: newIdempotencyKey(),
    })
    notice.value = archiving ? `房间「${room.name}」已归档` : `房间「${room.name}」已恢复`
    await loadAll()
  } catch (e) {
    handleError(e)
  } finally {
    busy.value = false
  }
}

async function toggleShelfArchived(shelf: StorageShelf) {
  const archiving = shelf.archived_at === null
  const message = archiving
    ? `确定归档书架「${shelf.name}」？归档后位置只读，仍被副本引用时会被拒绝。`
    : `确定恢复书架「${shelf.name}」？`
  if (!window.confirm(message)) return
  busy.value = true
  try {
    await updateShelf(shelf.id, {
      version: shelf.version,
      archived: archiving,
      idempotency_key: newIdempotencyKey(),
    })
    notice.value = archiving ? `书架「${shelf.name}」已归档` : `书架「${shelf.name}」已恢复`
    await loadAll()
  } catch (e) {
    handleError(e)
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <section class="storage-view">
    <h1>实体书架</h1>
    <p class="muted">
      这里登记家中的实体房间与书架（位置档案）；浏览藏书封面请使用首页「书架」封面墙。
      编号约定：面向书架正面，层从上到下、格子从左到右。
    </p>

    <div class="filter-bar">
      <label class="sr-only" for="storage-keyword">按房间或书架名称筛选</label>
      <input
        id="storage-keyword"
        v-model="keyword"
        type="text"
        placeholder="筛选房间 / 书架名称或编号…"
      />
      <label class="archive-toggle">
        <input
          type="checkbox"
          :checked="includeArchived"
          data-field="include_archived"
          @change="toggleArchivedFilter"
        />
        显示已归档
      </label>
      <button v-if="isOwner" type="button" class="btn primary" :disabled="busy" @click="roomForm = { room: null }">
        新建房间
      </button>
    </div>

    <StorageRoomForm
      v-if="roomForm"
      :room="roomForm.room"
      :busy="busy"
      @save="submitRoomForm"
      @cancel="roomForm = null"
    />

    <StorageShelfForm
      v-if="shelfForm"
      :shelf="shelfForm.shelf"
      :room-name="shelfForm.roomName"
      :busy="busy"
      @save="submitShelfForm"
      @cancel="shelfForm = null"
    />

    <ShelfLayoutEditor
      v-if="layoutShelfId !== null"
      :shelf-id="layoutShelfId"
      @close="layoutShelfId = null"
      @saved="onLayoutSaved"
    />

    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <div v-if="loading" class="muted">加载中…</div>

    <!-- 空态：Owner 引导建档；Member 如实说明尚未配置（LOC-01 冻结口径） -->
    <div v-else-if="rooms.length === 0" class="empty" data-testid="storage-empty">
      <template v-if="isOwner">
        <p>还没有实体书架档案。先创建第一个房间，再在房间里建书架；无照片也可以先建档。</p>
        <button type="button" class="btn primary" @click="roomForm = { room: null }">创建第一个房间</button>
      </template>
      <p v-else>尚未配置实体书架。</p>
    </div>

    <div v-else-if="visibleRooms.length === 0" class="empty muted">没有匹配「{{ keyword }}」的房间或书架。</div>

    <div v-else class="room-list">
      <section v-for="entry in visibleRooms" :key="entry.room.id" class="room-group" :data-room="entry.room.id">
        <header class="room-header">
          <h2>
            {{ entry.room.name }}
            <span class="code">编号 {{ entry.room.code }}</span>
            <span v-if="entry.room.archived_at" class="badge archived">已归档</span>
          </h2>
          <p v-if="entry.room.description" class="muted">{{ entry.room.description }}</p>
          <p class="stats">
            已分配位置 {{ statsOf(entry.room).assigned_copies }} 册 ·
            在架 {{ statsOf(entry.room).on_shelf_copies }} 册 ·
            涉及书目 {{ statsOf(entry.room).books_involved }} 本
          </p>
          <div v-if="isOwner" class="actions">
            <button type="button" class="btn" :disabled="busy" @click="roomForm = { room: entry.room }">编辑房间</button>
            <button
              type="button"
              class="btn"
              :disabled="busy"
              @click="toggleRoomArchived(entry.room)"
            >{{ entry.room.archived_at ? '恢复房间' : '归档房间' }}</button>
            <button
              v-if="!entry.room.archived_at"
              type="button"
              class="btn"
              :disabled="busy"
              @click="shelfForm = { shelf: null, roomId: entry.room.id, roomName: entry.room.name }"
            >新建书架</button>
          </div>
        </header>

        <p v-if="entry.shelves.length === 0" class="muted room-empty">
          该房间暂无书架<template v-if="isOwner && !entry.room.archived_at">，可点击「新建书架」建档</template>。
        </p>

        <div v-else class="shelf-grid">
          <article
            v-for="shelf in entry.shelves"
            :key="shelf.id"
            class="shelf-card"
            :class="{ archived: shelf.archived_at !== null }"
            :data-shelf="shelf.id"
          >
            <div class="shelf-photo">
              <img
                v-if="primaryPhotoUrl(shelf.id)"
                :src="primaryPhotoUrl(shelf.id)!"
                :alt="`${shelf.name} 外观照片`"
                loading="lazy"
              />
              <div v-else class="photo-placeholder">暂无外观照片</div>
            </div>
            <div class="shelf-info">
              <h3>
                <RouterLink :to="`/storage/shelves/${shelf.id}`" class="shelf-link">{{ shelf.name }}</RouterLink>
                <span class="code">{{ shelf.code }}</span>
                <span v-if="shelf.archived_at" class="badge archived">已归档</span>
              </h3>
              <p v-if="shelf.position_note" class="muted">{{ shelf.position_note }}</p>
              <p class="muted structure">
                {{ layerCountOf(shelf.id) }} 层 · {{ cellCountOf(shelf.id) }} 格
              </p>
              <p class="stats" v-if="shelfStats(shelf.id)">
                已分配位置 {{ shelfStats(shelf.id)!.assigned_copies }} 册 ·
                在架 {{ shelfStats(shelf.id)!.on_shelf_copies }} 册 ·
                涉及书目 {{ shelfStats(shelf.id)!.books_involved }} 本
              </p>
              <div v-if="isOwner" class="actions">
                <button type="button" class="btn" :disabled="busy" @click="shelfForm = { shelf, roomId: entry.room.id, roomName: entry.room.name }">编辑</button>
                <button
                  v-if="!shelf.archived_at && !entry.room.archived_at"
                  type="button"
                  class="btn"
                  :disabled="busy"
                  @click="layoutShelfId = shelf.id"
                >布局</button>
                <button
                  type="button"
                  class="btn"
                  :disabled="busy"
                  @click="toggleShelfArchived(shelf)"
                >{{ shelf.archived_at ? '恢复' : '归档' }}</button>
              </div>
            </div>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.storage-view { max-width: 960px; margin: 0 auto; padding: var(--space-3, 12px); }
.muted { color: var(--text-muted, #5a6878); font-size: 0.92rem; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
.filter-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin: 12px 0; }
.filter-bar input[type='text'] {
  flex: 1 1 220px; padding: 8px 10px;
  border: 1px solid var(--border, #d7dee8); border-radius: 8px;
}
.archive-toggle { display: flex; gap: 6px; align-items: center; font-size: 0.92rem; }
.notice { color: #1c6b48; }
.empty {
  text-align: center; padding: 48px 20px;
  background: var(--card-bg, #fff); border: 1px dashed var(--border, #d7dee8); border-radius: 12px;
}
.room-group {
  background: var(--card-bg, #fff); border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px; padding: 16px; margin-bottom: 16px;
}
.room-header h2 { margin: 0 0 4px; font-size: 1.1rem; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.code { font-size: 0.82rem; color: var(--text-muted, #5a6878); font-weight: 400; }
.badge.archived {
  font-size: 0.78rem; color: #8a5a19; background: #fdf0dc;
  border: 1px solid #ecd9b8; border-radius: 999px; padding: 1px 8px;
}
.stats { font-size: 0.88rem; margin: 4px 0; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.btn {
  padding: 6px 12px; border-radius: 8px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.room-empty { margin: 8px 0 0; }
.shelf-grid {
  display: grid; gap: 12px; margin-top: 12px;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
}
.shelf-card {
  border: 1px solid var(--border, #e2e8f0); border-radius: 10px; overflow: hidden;
  display: flex; flex-direction: column;
}
.shelf-card.archived { opacity: 0.75; }
.shelf-photo { aspect-ratio: 4 / 3; background: var(--bg-muted, #f1f4f8); display: flex; align-items: center; justify-content: center; }
.shelf-photo img { width: 100%; height: 100%; object-fit: cover; display: block; }
.photo-placeholder { color: var(--text-muted, #5a6878); font-size: 0.88rem; }
.shelf-info { padding: 10px 12px; }
.shelf-info h3 { margin: 0 0 4px; font-size: 1rem; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.shelf-info p { margin: 2px 0; }
</style>
