<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { StorageShelf } from '@/types/models'
import type { ShelfLayerInput } from '@/stores/storage'

/**
 * 书架建档/编辑表单（LOC-10）。仅 Owner 渲染。
 * 建档时可指定层数与每层格数（§7.1 默认每层 1 格，允许每层不同）；
 * 编辑仅改元数据，层格结构调整属于布局编辑器（LOC-11）。
 */
const props = defineProps<{
  shelf: StorageShelf | null
  roomName: string
  busy: boolean
}>()

const emit = defineEmits<{
  save: [payload: {
    code: string
    name: string
    position_note: string | null
    sort_order: number
    layers?: ShelfLayerInput[]
  }]
  cancel: []
}>()

const code = ref('')
const name = ref('')
const positionNote = ref('')
const sortOrder = ref(0)
// 建档：层数与各层格数（每层至少 1 格）
const layerCount = ref(1)
const cellCounts = ref<number[]>([1])

watch(
  () => props.shelf,
  (shelf, prev) => {
    // 同一书架的冲突刷新只更新版本，不覆盖编辑中的草稿（BUG-305）。
    if (shelf && prev && prev.id === shelf.id) return
    code.value = shelf?.code ?? ''
    name.value = shelf?.name ?? ''
    positionNote.value = shelf?.position_note ?? ''
    sortOrder.value = shelf?.sort_order ?? 0
    layerCount.value = 1
    cellCounts.value = [1]
  },
  { immediate: true },
)

watch(layerCount, (n) => {
  const count = Math.min(Math.max(1, Math.floor(n) || 1), 20)
  if (count !== n) layerCount.value = count
  const next = cellCounts.value.slice(0, count)
  while (next.length < count) next.push(1)
  cellCounts.value = next
})

const layerPreviews = computed(() =>
  cellCounts.value.map((cells, i) => ({ index: i, cells })),
)

function submit() {
  const payload: {
    code: string
    name: string
    position_note: string | null
    sort_order: number
    layers?: ShelfLayerInput[]
  } = {
    code: code.value.trim(),
    name: name.value.trim(),
    position_note: positionNote.value.trim() || null,
    sort_order: sortOrder.value,
  }
  if (!props.shelf) {
    payload.layers = cellCounts.value.map((cells) => ({
      cells: Array.from({ length: Math.max(1, Math.floor(cells) || 1) }, () => ({})),
    }))
  }
  emit('save', payload)
}
</script>

<template>
  <form class="storage-form" @submit.prevent="submit">
    <h3>{{ shelf ? `编辑书架「${shelf.name}」` : `在「${roomName}」新建书架` }}</h3>
    <label>
      编号（家庭内唯一，如 A01；建档后保留）
      <input v-model="code" type="text" required maxlength="50" placeholder="如 A01" data-field="code" :disabled="busy" />
    </label>
    <label>
      名称
      <input v-model="name" type="text" required maxlength="100" placeholder="如 A 号书架" data-field="name" :disabled="busy" />
    </label>
    <label>
      方位说明（可选）
      <input v-model="positionNote" type="text" maxlength="200" placeholder="如 靠窗右侧" data-field="position_note" :disabled="busy" />
    </label>
    <label>
      排序（小的在前）
      <input v-model.number="sortOrder" type="number" data-field="sort_order" :disabled="busy" />
    </label>

    <template v-if="!shelf">
      <label>
        层数
        <input v-model.number="layerCount" type="number" min="1" max="20" data-field="layer_count" :disabled="busy" />
      </label>
      <fieldset class="layer-cells">
        <legend>每层格数（允许每层不同）</legend>
        <label v-for="lp in layerPreviews" :key="lp.index">
          第 {{ lp.index + 1 }} 层格数
          <input
            v-model.number="cellCounts[lp.index]"
            type="number"
            min="1"
            max="20"
            :data-field="`cells_${lp.index}`"
          />
        </label>
      </fieldset>
      <p class="muted">编号约定：面向书架正面，层从上到下、格子从左到右。保存后可在书架详情继续调整层格。</p>
    </template>
    <p v-else class="muted">层与格子的结构调整请在书架布局编辑中进行；此处仅修改编号、名称与方位。</p>

    <div class="form-actions">
      <button type="submit" class="btn primary" :disabled="busy">{{ shelf ? '保存修改' : '创建书架' }}</button>
      <button type="button" class="btn" :disabled="busy" @click="emit('cancel')">取消</button>
    </div>
  </form>
</template>

<style scoped>
.storage-form {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  padding: 16px;
  margin: 12px 0;
  display: grid;
  gap: 10px;
  max-width: 480px;
}
.storage-form h3 { margin: 0; font-size: 1rem; }
.storage-form label { display: grid; gap: 4px; font-size: 0.92rem; }
.storage-form input { padding: 8px 10px; border: 1px solid var(--border, #d7dee8); border-radius: 8px; }
.layer-cells { border: 1px dashed var(--border, #d7dee8); border-radius: 8px; padding: 8px 12px; display: grid; gap: 8px; }
.layer-cells legend { font-size: 0.88rem; color: var(--text-muted, #5a6878); padding: 0 4px; }
.muted { color: var(--text-muted, #5a6878); font-size: 0.88rem; margin: 0; }
.form-actions { display: flex; gap: 8px; }
.btn { padding: 8px 14px; border-radius: 8px; cursor: pointer; background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit; }
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
