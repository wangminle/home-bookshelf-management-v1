<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  StorageApiError,
  getShelf,
  newIdempotencyKey,
  putShelfLayout,
} from '@/stores/storage'
import type { LayoutPutPayload, LayoutLayerInput } from '@/stores/storage'
import type { StorageShelfDetail } from '@/types/models'

/**
 * 可变层格布局编辑器（LOC-11，仅 Owner 进入）。
 *
 * 契约要点（M0 冻结 + 修订记录 LOC-06）：
 * - PUT layout 全量表达：未列出的现存活动层/格视为归档请求，有副本引用 409；
 * - 改名/排序带回稳定 id 与 version，新增层/格不带 id，cell code 服务端自动编号；
 * - 409 PLACEMENT_CHANGED：刷新最新布局后请用户重新确认，不自动重放写操作；
 * - 幂等键必传；同一未变载荷失败重试复用同一 key（同 key 同摘要重放返回原回执）。
 */

interface EditableCell {
  id: number | null
  version: number | null
  /** 既有格子的稳定编号；新建格子为 null（服务端自动编号 C{n}） */
  code: string | null
  label: string
}

interface EditableLayer {
  id: number | null
  version: number | null
  label: string
  cells: EditableCell[]
}

const props = defineProps<{ shelfId: number }>()
const emit = defineEmits<{ close: []; saved: [] }>()

const loading = ref(true)
const busy = ref(false)
const shelf = ref<StorageShelfDetail | null>(null)
const layers = ref<EditableLayer[]>([])
const notice = ref('')
/** 结构化错误提示（LOCATION_IN_USE 指引等），内联展示不遮挡编辑内容 */
const errorText = ref('')

/** 上次失败提交的幂等键与载荷摘要：载荷未变时重试复用同一 key */
let lastFailed: { key: string; payload: string } | null = null

/** 层/格 id → 可读描述，用于把 LOCATION_IN_USE 的引用 id 翻译成用户可认的位置 */
const idLabels = new Map<number, string>()

function toEditable(detail: StorageShelfDetail): EditableLayer[] {
  return detail.layers.map((layer) => ({
    id: layer.id,
    version: layer.version,
    label: layer.label,
    cells: layer.cells.map((cell) => ({
      id: cell.id,
      version: cell.version,
      code: cell.code,
      label: cell.label,
    })),
  }))
}

function rebuildIdLabels(detail: StorageShelfDetail) {
  idLabels.clear()
  for (const layer of detail.layers) {
    idLabels.set(layer.id, `层「${layer.label}」`)
    for (const cell of layer.cells) {
      idLabels.set(cell.id, `层「${layer.label}」· 格 ${cell.code}「${cell.label}」`)
    }
  }
}

async function load() {
  loading.value = true
  try {
    const detail = await getShelf(props.shelfId)
    shelf.value = detail
    layers.value = toEditable(detail)
    rebuildIdLabels(detail)
  } catch (e) {
    handleError(e)
  } finally {
    loading.value = false
  }
}

// ── 结构编辑（按钮操作，不依赖拖拽，手机可用） ──

function addLayer() {
  layers.value.push({
    id: null,
    version: null,
    label: `第 ${layers.value.length + 1} 层`,
    cells: [{ id: null, version: null, code: null, label: '格 1' }],
  })
}

function removeLayer(index: number) {
  const layer = layers.value[index]
  if (layer.id !== null) {
    // 全量表达语义：从清单移除 = 保存时归档；不静默删格，先确认
    if (!window.confirm(`移除「${layer.label}」将在保存时归档该层；若仍有副本引用会被拒绝。确定移除？`)) {
      return
    }
  }
  layers.value.splice(index, 1)
}

function moveLayer(index: number, dir: -1 | 1) {
  const target = index + dir
  if (target < 0 || target >= layers.value.length) return
  const [layer] = layers.value.splice(index, 1)
  layers.value.splice(target, 0, layer)
}

function addCell(layer: EditableLayer) {
  layer.cells.push({
    id: null,
    version: null,
    code: null,
    label: `格 ${layer.cells.length + 1}`,
  })
}

function removeCell(layer: EditableLayer, index: number) {
  const cell = layer.cells[index]
  if (cell.id !== null) {
    if (!window.confirm(`移除格子 ${cell.code ?? ''}「${cell.label}」将在保存时归档该格；若仍有副本引用会被拒绝。确定移除？`)) {
      return
    }
  }
  layer.cells.splice(index, 1)
}

function moveCell(layer: EditableLayer, index: number, dir: -1 | 1) {
  const target = index + dir
  if (target < 0 || target >= layer.cells.length) return
  const [cell] = layer.cells.splice(index, 1)
  layer.cells.splice(target, 0, cell)
}

const canSave = computed(
  () => layers.value.length >= 1 && layers.value.every((l) => l.cells.length >= 1),
)

// ── 保存 ──

function buildPayload(): Omit<LayoutPutPayload, 'idempotency_key'> {
  return {
    shelf_version: shelf.value!.version,
    layers: layers.value.map<LayoutLayerInput>((layer, li) => ({
      // 既有层带回 id+version 保稳定 ID；新增层不带 id
      ...(layer.id !== null ? { id: layer.id, version: layer.version ?? undefined } : {}),
      label: layer.label.trim() || undefined,
      sort_order: li,
      cells: layer.cells.map((cell, ci) => ({
        ...(cell.id !== null ? { id: cell.id, version: cell.version ?? undefined } : {}),
        label: cell.label.trim() || undefined,
        sort_order: ci,
      })),
    })),
  }
}

/** LOCATION_IN_USE：把消息中的层/格 id 翻译成用户可认的位置描述 */
function enrichInUseMessage(message: string): string {
  const refs: string[] = []
  for (const match of message.matchAll(/(?:格子|层) (\d+)/g)) {
    const label = idLabels.get(Number(match[1]))
    if (label && !refs.includes(label)) refs.push(label)
  }
  const guide = '这些位置仍登记有副本，需先把副本移动到其他位置才能归档；副本移动功能将在后续版本提供，当前请撤销对这些层/格的移除后再保存。'
  if (refs.length === 0) return `${message}。${guide}`
  return `仍被副本引用：${refs.join('；')}。${guide}`
}

function handleError(e: unknown) {
  if (e instanceof StorageApiError) {
    if (e.code === 'PLACEMENT_CHANGED') {
      // §7.3：版本冲突不自动重放；刷新最新布局后请用户重新确认
      errorText.value = '布局已被其他操作修改，已载入最新版本，请重新核对后再保存。'
      lastFailed = null
      load()
      return
    }
    if (e.code === 'LOCATION_IN_USE') {
      errorText.value = enrichInUseMessage(e.message)
      return
    }
    errorText.value = e.message
    return
  }
  errorText.value = e instanceof Error ? e.message : '保存失败'
}

async function save() {
  if (!shelf.value || !canSave.value || busy.value) return
  const payloadJson = JSON.stringify(buildPayload())
  // 同一载荷失败重试复用同一幂等键（同 key 同摘要重放返回原回执，不重复建格）；
  // 载荷变化后必须用新 key（同 key 不同摘要会被判 IDEMPOTENCY_CONFLICT）。
  const key = lastFailed && lastFailed.payload === payloadJson
    ? lastFailed.key
    : newIdempotencyKey()
  busy.value = true
  errorText.value = ''
  try {
    const detail = await putShelfLayout(props.shelfId, {
      ...JSON.parse(payloadJson),
      idempotency_key: key,
    })
    lastFailed = null
    shelf.value = detail
    layers.value = toEditable(detail)
    rebuildIdLabels(detail)
    notice.value = '布局已保存'
    emit('saved')
  } catch (e) {
    lastFailed = { key, payload: payloadJson }
    handleError(e)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="layout-editor" aria-label="书架布局编辑">
    <h3>布局编辑{{ shelf ? `：${shelf.name}（${shelf.code}）` : '' }}</h3>
    <p class="muted">
      编号约定：面向书架正面，层从上到下、格子从左到右。
      移除已有的层/格子将在保存时归档（编号保留不复用）；仍有副本引用的层/格不能移除。
    </p>

    <div v-if="loading" class="muted">加载中…</div>
    <template v-else-if="shelf">
      <div v-for="(layer, li) in layers" :key="layer.id ?? `new-${li}`" class="layer-block" :data-layer-index="li">
        <header class="layer-header">
          <span class="layer-pos">第 {{ li + 1 }} 层</span>
          <label class="sr-only" :for="`layer-label-${li}`">第 {{ li + 1 }} 层名称</label>
          <input :id="`layer-label-${li}`" v-model="layer.label" type="text" maxlength="50" data-field="layer-label" />
          <span class="mini-actions">
            <button type="button" :disabled="busy || li === 0" @click="moveLayer(li, -1)">上移</button>
            <button type="button" :disabled="busy || li === layers.length - 1" @click="moveLayer(li, 1)">下移</button>
            <button type="button" :disabled="busy" @click="removeLayer(li)">移除层</button>
          </span>
        </header>
        <ul class="cell-list">
          <li v-for="(cell, ci) in layer.cells" :key="cell.id ?? `new-${ci}`" :data-cell-index="ci">
            <span class="cell-code">{{ cell.code ?? '自动编号' }}</span>
            <label class="sr-only" :for="`cell-label-${li}-${ci}`">第 {{ li + 1 }} 层第 {{ ci + 1 }} 格名称</label>
            <input :id="`cell-label-${li}-${ci}`" v-model="cell.label" type="text" maxlength="50" data-field="cell-label" />
            <span class="mini-actions">
              <button type="button" :disabled="busy || ci === 0" @click="moveCell(layer, ci, -1)">左移</button>
              <button type="button" :disabled="busy || ci === layer.cells.length - 1" @click="moveCell(layer, ci, 1)">右移</button>
              <button type="button" :disabled="busy" @click="removeCell(layer, ci)">移除</button>
            </span>
          </li>
        </ul>
        <button type="button" class="btn" :disabled="busy" @click="addCell(layer)">添加格子</button>
      </div>

      <button type="button" class="btn" :disabled="busy" @click="addLayer">添加层</button>

      <p v-if="!canSave" class="warn">至少保留 1 层，且每层至少 1 格。</p>
      <p v-if="errorText" class="error-box" role="alert">{{ errorText }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>

      <div class="actions">
        <button type="button" class="btn primary" :disabled="busy || !canSave" @click="save">保存布局</button>
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">关闭</button>
      </div>
    </template>
    <div class="actions" v-else>
      <button type="button" class="btn" @click="emit('close')">关闭</button>
    </div>
  </section>
</template>

<style scoped>
.layout-editor {
  background: var(--card-bg, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 12px;
  padding: 16px;
  margin: 12px 0;
}
.layout-editor h3 { margin: 0 0 4px; font-size: 1rem; }
.muted { color: var(--text-muted, #5a6878); font-size: 0.88rem; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
.layer-block {
  border: 1px dashed var(--border, #d7dee8);
  border-radius: 10px;
  padding: 10px 12px;
  margin: 10px 0;
}
.layer-header { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.layer-pos { font-weight: 600; font-size: 0.92rem; }
.layer-header input, .cell-list input {
  padding: 6px 10px; border: 1px solid var(--border, #d7dee8); border-radius: 8px;
  min-width: 0; flex: 1 1 120px;
}
.cell-list { list-style: none; padding: 0; margin: 8px 0; display: grid; gap: 6px; }
.cell-list li { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.cell-code {
  font-size: 0.82rem; color: var(--text-muted, #5a6878);
  min-width: 56px; font-variant-numeric: tabular-nums;
}
.mini-actions { display: flex; gap: 4px; }
.mini-actions button {
  padding: 4px 8px; font-size: 0.82rem; border-radius: 6px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.mini-actions button:disabled { opacity: 0.5; cursor: not-allowed; }
.btn {
  padding: 6px 12px; border-radius: 8px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
.warn { color: #8a5a19; font-size: 0.88rem; }
.error-box {
  background: #fdecea; border: 1px solid #f5c6c0; border-radius: 8px;
  padding: 10px 12px; color: #b3261e; font-size: 0.92rem;
}
.notice { color: #1c6b48; }
.actions { display: flex; gap: 8px; margin-top: 12px; }
</style>
