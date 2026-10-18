<script setup lang="ts">
import { ref, watch } from 'vue'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { checkLocationOperation, sendLocationOperation, pendingLocationOperations } from '@/stores/storage'
import type { LocationOperation } from '@/stores/storage'

const props = defineProps<{ bookIds?: number[] }>()
const emit = defineEmits<{ completed: [result: unknown]; changed: [] }>()
const operations = ref<LocationOperation[]>([])
const busy = ref('')
const error = ref('')
const notice = ref('')

function refresh() {
  operations.value = []
  if (sessionRole.value !== 'owner' || !sessionMemberId.value) return
  try {
    operations.value = pendingLocationOperations().filter((op) => {
      if (!props.bookIds?.length) return true
      if (op.kind === 'move') return false // 移动记录保留在统一恢复页，避免猜测书目关系
      const items = op.payload.items as { book_id: number }[]
      return items.some((i) => props.bookIds!.includes(i.book_id))
    })
  } catch (e) { error.value = e instanceof Error ? e.message : '读取待核对操作失败' }
}

async function recover(op: LocationOperation, replay: boolean) {
  if (busy.value) return
  busy.value = op.key
  error.value = ''; notice.value = ''
  try {
    let result = await checkLocationOperation(op)
    if (result === null && replay) result = await sendLocationOperation(op)
    if (result === null) {
      notice.value = '暂未查到完成回执，结果仍未确认。请稍后再查，或重试原操作；不会另起操作。'
    } else {
      notice.value = '已确认完成，无需再次登记。'
      emit('completed', result)
    }
  } catch (e) { error.value = e instanceof Error ? e.message : '恢复失败，原操作仍保留' }
  finally { busy.value = ''; refresh(); emit('changed') }
}

watch([sessionRole, sessionMemberId, () => props.bookIds], refresh, { immediate: true })
defineExpose({ refresh })
</script>

<template>
  <section v-if="sessionRole === 'owner' && (operations.length || error || notice)" class="operation-recovery" aria-label="恢复位置操作">
    <h2 v-if="operations.length">有 {{ operations.length }} 项位置操作待核对</h2>
    <p v-if="operations.length">请求可能已成功。请先核对回执；刷新后仍会保留原操作。</p>
    <ul>
      <li v-for="op in operations" :key="op.key">
        <strong>{{ op.label }}</strong>
        <span class="muted">{{ new Date(op.createdAt).toLocaleString('zh-CN') }}</span>
        <div class="recovery-actions">
          <button type="button" data-action="check" :disabled="!!busy" @click="recover(op, false)">查询回执</button>
          <button type="button" data-action="replay" :disabled="!!busy" @click="recover(op, true)">重试原操作</button>
        </div>
      </li>
    </ul>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<style scoped>
.operation-recovery { padding: 16px; margin: 16px 0; border: 1px solid var(--border); border-left: 4px solid var(--primary); background: var(--card-bg); border-radius: 8px; }
h2 { font-size: 1rem; margin: 0 0 8px; }
p { margin: 8px 0; }
ul { padding: 0; list-style: none; }
li { display: grid; gap: 6px; padding: 12px 0; border-bottom: 1px solid var(--border); }
.recovery-actions { display: flex; gap: 8px; flex-wrap: wrap; }
button { min-height: 44px; padding: 8px 14px; border: 1px solid var(--border); border-radius: 6px; background: var(--card-bg); color: var(--text); cursor: pointer; }
button:disabled { opacity: .5; cursor: wait; }
.muted { font-size: .85rem; color: var(--text-muted); }
[role=alert] { color: var(--error, #b3261e); }
</style>
