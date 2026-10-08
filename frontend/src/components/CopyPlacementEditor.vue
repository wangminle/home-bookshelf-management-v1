<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  StorageApiError,
  newIdempotencyKey,
  setCopyPlacement,
} from '@/stores/storage'
import type { PlacementTargetInput } from '@/stores/storage'
import PlacementTargetPicker from '@/components/PlacementTargetPicker.vue'
import type { BookCopy } from '@/types/models'

/**
 * 单册位置编辑（LOC-15 对接 LOC-13 PATCH placement）。
 * - 设置/移动：级联选择目标（仅到书架或到格子），带 placement_version 与幂等键；
 * - 清除：必须显式选择「保留旧文字」或「一并清空」（契约修订 LOC-13，不默认其一）；
 * - 409 PLACEMENT_CHANGED：提示刷新，由父组件重载，不自动重放。
 */
const props = defineProps<{
  bookId: number
  copy: BookCopy
}>()

const emit = defineEmits<{ saved: []; close: []; conflict: [] }>()

type Mode = 'set' | 'clear'
const mode = ref<Mode>('set')
const target = ref<PlacementTargetInput | null>(null)
/** 清除分支：null=未选择（禁止提交），true=保留旧文字，false=一并清空 */
const keepLegacy = ref<boolean | null>(null)
const busy = ref(false)
const errorText = ref('')

/**
 * 模式切换时重置两个分支的隐藏状态：选择器因 v-if 重挂载显示为空，
 * 若父级 target 保留旧值，保存会把副本移动到界面上未选中的旧位置；
 * 清除分支的 keepLegacy 同理重置，避免残留上次选择。
 */
watch(mode, () => {
  target.value = null
  keepLegacy.value = null
})

async function submit() {
  if (busy.value) return
  errorText.value = ''
  // BUG-298：清除后的 placement=null 仍有真实版本；兼容旧的已定位响应。
  const placementVersion = props.copy.placement_version ?? props.copy.placement?.version
  if (placementVersion === undefined || !Number.isInteger(placementVersion) || placementVersion < 1) {
    errorText.value = '未取得副本的最新位置版本，请刷新详情后重试。'
    emit('conflict')
    return
  }
  let payload
  if (mode.value === 'set') {
    if (!target.value) {
      errorText.value = '请先选择目标书架'
      return
    }
    payload = {
      placement_version: placementVersion,
      target: target.value,
      idempotency_key: newIdempotencyKey(),
    }
  } else {
    if (keepLegacy.value === null) {
      errorText.value = '清除位置前请选择「保留旧文字」或「一并清空」'
      return
    }
    payload = {
      placement_version: placementVersion,
      target: null,
      keep_legacy_location: keepLegacy.value,
      idempotency_key: newIdempotencyKey(),
    }
  }
  busy.value = true
  try {
    await setCopyPlacement(props.bookId, props.copy.id, payload)
    emit('saved')
  } catch (e) {
    if (e instanceof StorageApiError && e.code === 'PLACEMENT_CHANGED') {
      errorText.value = '该副本位置已被其他操作修改，已为你刷新最新数据，请核对后重试。'
      emit('conflict')
      return
    }
    errorText.value = e instanceof Error ? e.message : '保存失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="placement-editor" @submit.prevent="submit">
    <h3>编辑副本 #{{ copy.id }} 的位置</h3>
    <p v-if="copy.location_display || copy.location" class="muted">
      当前：{{ copy.location_display || copy.location }}
    </p>

    <div class="mode-row" role="radiogroup" aria-label="操作类型">
      <label>
        <input v-model="mode" type="radio" value="set" data-field="mode-set" />
        设置/移动位置
      </label>
      <label>
        <input v-model="mode" type="radio" value="clear" data-field="mode-clear" />
        清除位置
      </label>
    </div>

    <PlacementTargetPicker
      v-if="mode === 'set'"
      @update:target="target = $event"
    />

    <div v-else class="clear-branch">
      <p class="muted">清除后副本不再有结构化位置。旧位置文字（{{ copy.location || '无' }}）：</p>
      <label>
        <input v-model="keepLegacy" type="radio" :value="true" data-field="keep-legacy" />
        保留旧文字（仍作为参考显示）
      </label>
      <label>
        <input v-model="keepLegacy" type="radio" :value="false" data-field="clear-legacy" />
        一并清空旧文字
      </label>
    </div>

    <p v-if="errorText" class="error-box" role="alert">{{ errorText }}</p>

    <div class="actions">
      <button type="submit" class="btn primary" :disabled="busy">保存位置</button>
      <button type="button" class="btn" :disabled="busy" @click="emit('close')">取消</button>
    </div>
  </form>
</template>

<style scoped>
.placement-editor {
  border: 1px solid var(--border, #e2e8f0); border-radius: 10px;
  padding: 12px 14px; margin-top: 8px; display: grid; gap: 10px;
  background: var(--card-bg, #fff);
}
.placement-editor h3 { margin: 0; font-size: 0.98rem; }
.muted { color: var(--text-muted, #5a6878); font-size: 0.88rem; margin: 0; }
.mode-row { display: flex; gap: 16px; flex-wrap: wrap; }
.mode-row label, .clear-branch label { display: flex; gap: 6px; align-items: center; font-size: 0.92rem; }
.clear-branch { display: grid; gap: 6px; }
.error-box {
  background: #fdecea; border: 1px solid #f5c6c0; border-radius: 8px;
  padding: 8px 12px; color: #b3261e; font-size: 0.9rem; margin: 0;
}
.actions { display: flex; gap: 8px; }
.btn {
  padding: 6px 12px; border-radius: 8px; cursor: pointer;
  background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit;
}
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
