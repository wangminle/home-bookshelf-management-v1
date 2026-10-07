<script setup lang="ts">
import { onMounted, ref } from 'vue'
import {
  getShelf,
  listRooms,
  listShelves,
} from '@/stores/storage'
import type {
  PlacementTargetInput,
} from '@/stores/storage'
import type {
  ShelfLayer,
  StorageRoomWithStats,
  StorageShelf,
  StorageShelfDetail,
} from '@/types/models'

/**
 * 位置目标级联选择器（LOC-15）：房间 → 书架 → 层 → 格。
 * 仅到书架允许（格留空 = 格子待登记，LOC-01 冻结）；只列出未归档的房间/书架/层/格。
 */
const emit = defineEmits<{
  'update:target': [target: PlacementTargetInput | null]
}>()

const rooms = ref<StorageRoomWithStats[]>([])
const shelves = ref<StorageShelf[]>([])
const shelfDetail = ref<StorageShelfDetail | null>(null)

const roomId = ref<number | ''>('')
const shelfId = ref<number | ''>('')
const layerId = ref<number | ''>('')
const cellId = ref<number | ''>('')

const loadError = ref('')

async function onRoomChange() {
  shelves.value = []
  shelfDetail.value = null
  shelfId.value = ''
  layerId.value = ''
  cellId.value = ''
  emitTarget()
  if (roomId.value === '') return
  try {
    shelves.value = (await listShelves(Number(roomId.value), false)).items
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载书架失败'
  }
}

async function onShelfChange() {
  shelfDetail.value = null
  layerId.value = ''
  cellId.value = ''
  emitTarget()
  if (shelfId.value === '') return
  try {
    shelfDetail.value = await getShelf(Number(shelfId.value))
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载书架结构失败'
  }
}

function onLayerChange() {
  cellId.value = ''
  emitTarget()
}

function onCellChange() {
  emitTarget()
}

function currentLayer(): ShelfLayer | null {
  if (!shelfDetail.value || layerId.value === '') return null
  return shelfDetail.value.layers.find((l) => l.id === Number(layerId.value)) ?? null
}

function emitTarget() {
  if (shelfId.value === '') {
    emit('update:target', null)
    return
  }
  emit('update:target', {
    shelf_id: Number(shelfId.value),
    cell_id: cellId.value === '' ? null : Number(cellId.value),
  })
}

onMounted(async () => {
  try {
    rooms.value = (await listRooms(false)).items
  } catch (e) {
    loadError.value = e instanceof Error ? e.message : '加载房间失败'
  }
})
</script>

<template>
  <div class="target-picker">
    <label>
      房间
      <select v-model="roomId" data-field="room" @change="onRoomChange">
        <option value="">请选择房间</option>
        <option v-for="r in rooms" :key="r.id" :value="r.id">{{ r.name }}（{{ r.code }}）</option>
      </select>
    </label>
    <label v-if="roomId !== ''">
      书架
      <select v-model="shelfId" data-field="shelf" @change="onShelfChange">
        <option value="">请选择书架</option>
        <option v-for="s in shelves" :key="s.id" :value="s.id">{{ s.name }}（{{ s.code }}）</option>
      </select>
    </label>
    <template v-if="shelfDetail">
      <label>
        层（选择具体格子时先选层）
        <select v-model="layerId" data-field="layer" @change="onLayerChange">
          <option value="">仅到书架（格子待登记）</option>
          <option v-for="l in shelfDetail.layers" :key="l.id" :value="l.id">{{ l.label }}</option>
        </select>
      </label>
      <label v-if="currentLayer()">
        格子
        <select v-model="cellId" data-field="cell" @change="onCellChange">
          <option value="">仅到书架（格子待登记）</option>
          <option v-for="c in currentLayer()!.cells" :key="c.id" :value="c.id">{{ c.code }} {{ c.label }}</option>
        </select>
      </label>
    </template>
    <p v-if="loadError" class="picker-error" role="alert">{{ loadError }}</p>
  </div>
</template>

<style scoped>
.target-picker { display: grid; gap: 8px; }
.target-picker label { display: grid; gap: 4px; font-size: 0.9rem; }
.target-picker select {
  padding: 8px 10px; border: 1px solid var(--border, #d7dee8); border-radius: 8px;
  background: var(--card-bg, #fff); color: inherit;
}
.picker-error { color: #b3261e; font-size: 0.88rem; margin: 0; }
</style>
