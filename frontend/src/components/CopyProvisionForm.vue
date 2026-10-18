<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useMembersStore } from '@/stores/members'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { StorageApiError, pendingLocationOperations, prepareLocationOperation, sendLocationOperation } from '@/stores/storage'
import type { PlacementTargetInput } from '@/stores/storage'
import PlacementTargetPicker from '@/components/PlacementTargetPicker.vue'
import LocationOperationRecovery from '@/components/LocationOperationRecovery.vue'

/** 册数由用户显式清点确认；写入前持久化原操作，未知结果只能查询或重放。 */
const props = defineProps<{ bookId: number }>()
const emit = defineEmits<{ saved: [created: number]; close: []; conflict: [] }>()
const members = useMembersStore()
const count = ref(0)
const ownerMemberId = ref<number | ''>('')
const target = ref<PlacementTargetInput | null>(null)
const targetDisplay = ref('')
const busy = ref(false)
const errorText = ref('')
const journalError = ref('')
const hasPending = ref(false)
const recovery = ref<InstanceType<typeof LocationOperationRecovery> | null>(null)
const isOwner = computed(() => sessionRole.value === 'owner' &&
  Number.isInteger(sessionMemberId.value) && (sessionMemberId.value ?? 0) > 0)
const locked = computed(() => busy.value || !isOwner.value || !!journalError.value || hasPending.value)
const canSubmit = computed(() => !locked.value && Number.isInteger(count.value) &&
  count.value >= 1 && count.value <= 100 && Number(ownerMemberId.value) > 0 && target.value !== null)

function refreshPending() {
  hasPending.value = false
  journalError.value = ''
  if (!isOwner.value) {
    journalError.value = '请使用有效的 Owner 账号登记实体副本。'
    return
  }
  try {
    hasPending.value = pendingLocationOperations().some((op) => op.kind === 'provision' &&
      Array.isArray(op.payload.items) && op.payload.items.some((item: any) => item.book_id === props.bookId))
  } catch (e) {
    journalError.value = e instanceof Error ? e.message : '无法读取已保存的位置操作'
  }
}
watch([() => props.bookId, sessionMemberId, sessionRole], () => {
  count.value = 0
  ownerMemberId.value = ''
  target.value = null
  targetDisplay.value = ''
  errorText.value = ''
  refreshPending()
}, { immediate: true })

function onRecovered(result: unknown) {
  refreshPending()
  const item = (result as { items?: { book_id: number; created: number[] }[] } | null)?.items?.find((row) => row.book_id === props.bookId)
  const created = item?.created.length
  if (typeof created === 'number' && Number.isInteger(created) && created >= 0) {
    errorText.value = ''
    emit('saved', created)
  }
}

async function submit() {
  if (busy.value) return
  refreshPending()
  if (!canSubmit.value) return
  errorText.value = ''
  busy.value = true
  const items = [{ book_id: props.bookId, new_copies: {
    count: count.value, owner_member_id: Number(ownerMemberId.value), target: target.value!,
  } }]
  try {
    const op = prepareLocationOperation('provision', { items }, `书目 #${props.bookId} 补录 ${count.value} 册（${members.members.find((m) => m.id === Number(ownerMemberId.value))?.name ?? '所选成员'}） → ${targetDisplay.value || '所选书架/格子'}`)
    const result = await sendLocationOperation(op) as { created: number }
    emit('saved', result.created)
  } catch (e) {
    if (e instanceof StorageApiError && e.code === 'PLACEMENT_CHANGED') {
      errorText.value = '位置数据已被其他操作修改，已为你刷新最新数据，请核对后重试。'
      emit('conflict')
    } else {
      errorText.value = e instanceof Error ? e.message : '补录结果未确认'
    }
  } finally {
    busy.value = false
    refreshPending()
    recovery.value?.refresh()
  }
}
</script>

<template>
  <form class="provision-form" @submit.prevent="submit">
    <h3>补录实体副本</h3>
    <p class="muted">请按实际清点确认实体册数；系统不会按书目或照片数量自动推定。</p>
    <fieldset :disabled="locked">
    <label>
      本次新增实体册数
      <input v-model.number="count" type="number" min="0" max="100" data-field="count" />
    </label>
    <label>
      归属成员
      <select v-model="ownerMemberId" data-field="owner">
        <option value="">请选择成员</option>
        <option v-for="m in members.members" :key="m.id" :value="m.id">{{ m.name }}</option>
      </select>
    </label>

    <PlacementTargetPicker @update:target="target = $event" @update:display="targetDisplay = $event" />
    </fieldset>

    <p v-if="journalError" class="error-box" role="alert">{{ journalError }}</p>
    <p v-if="hasPending" class="muted">已有结果未确认的登记，输入已锁定。请查询回执或重试原操作后再继续。</p>
    <LocationOperationRecovery ref="recovery" :book-ids="[bookId]" @completed="onRecovered" @changed="refreshPending" />

    <p v-if="errorText" class="error-box" role="alert">{{ errorText }}</p>

    <div class="actions">
      <button type="submit" class="btn primary" :disabled="!canSubmit">确认补录</button>
      <button type="button" class="btn" :disabled="busy" @click="emit('close')">取消</button>
    </div>
  </form>
</template>

<style scoped>
.provision-form {
  border: 1px solid var(--border, #e2e8f0); border-radius: 10px;
  padding: 12px 14px; margin-top: 8px; display: grid; gap: 10px;
  background: var(--card-bg, #fff); max-width: 480px;
}
.provision-form fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: 10px; min-width: 0; }
.provision-form h3 { margin: 0; font-size: 0.98rem; }
.muted { color: var(--text-muted, #5a6878); font-size: 0.88rem; margin: 0; }
.provision-form label { display: grid; gap: 4px; font-size: 0.9rem; }
.provision-form input, .provision-form select {
  padding: 8px 10px; border: 1px solid var(--border, #d7dee8); border-radius: 8px;
  background: var(--card-bg, #fff); color: inherit;
}
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
