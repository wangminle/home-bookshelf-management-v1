<script setup lang="ts">
import { computed, ref } from 'vue'
import { useMembersStore } from '@/stores/members'
import {
  StorageApiError,
  forgetProvisionIdempotencyKey,
  provisionCopies,
  reuseProvisionIdempotencyKey,
} from '@/stores/storage'
import type { PlacementTargetInput } from '@/stores/storage'
import PlacementTargetPicker from '@/components/PlacementTargetPicker.vue'

/**
 * 实体副本补录表单（LOC-15 对接 LOC-14 POST /storage/copies）。
 * 册数只能由用户显式确认（LOC-01 冻结：不按书目/照片数推定）；
 * 归属成员必选；目标位置仅到书架或到格子；幂等键必传。
 */
const props = defineProps<{ bookId: number }>()
const emit = defineEmits<{ saved: [created: number]; close: []; conflict: [] }>()

const members = useMembersStore()

const count = ref(1)
const ownerMemberId = ref<number | ''>('')
const target = ref<PlacementTargetInput | null>(null)
const busy = ref(false)
const errorText = ref('')

const canSubmit = computed(
  () => count.value >= 1 && ownerMemberId.value !== '' && target.value !== null,
)

async function submit() {
  if (busy.value || !canSubmit.value) return
  errorText.value = ''
  busy.value = true
  const items = [{
    book_id: props.bookId,
    new_copies: {
      count: Math.floor(count.value),
      owner_member_id: Number(ownerMemberId.value),
      target: target.value!,
    },
  }]
  try {
    const result = await provisionCopies({
      idempotency_key: reuseProvisionIdempotencyKey(props.bookId, items),
      items,
    })
    // 先确认回执再丢键。HTTP 201 但 JSON 截断时 result 不可用，丢掉原键会让
    // 同载荷重试变成新的补录（BUG-299）。
    if (typeof result?.created !== 'number') {
      throw new Error('补录回执无法确认，请重试同一操作（不会另建副本）')
    }
    forgetProvisionIdempotencyKey(props.bookId, items)
    emit('saved', result.created)
  } catch (e) {
    if (e instanceof StorageApiError && e.code === 'PLACEMENT_CHANGED') {
      errorText.value = '位置数据已被其他操作修改，已为你刷新最新数据，请核对后重试。'
      emit('conflict')
      return
    }
    errorText.value = e instanceof Error ? e.message : '补录失败'
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="provision-form" @submit.prevent="submit">
    <h3>补录实体副本</h3>
    <p class="muted">请按实际清点确认实体册数；系统不会按书目或照片数量自动推定。</p>
    <label>
      实际实体册数
      <input v-model.number="count" type="number" min="1" max="100" data-field="count" />
    </label>
    <label>
      归属成员
      <select v-model="ownerMemberId" data-field="owner">
        <option value="">请选择成员</option>
        <option v-for="m in members.members" :key="m.id" :value="m.id">{{ m.name }}</option>
      </select>
    </label>

    <PlacementTargetPicker @update:target="target = $event" />

    <p v-if="errorText" class="error-box" role="alert">{{ errorText }}</p>

    <div class="actions">
      <button type="submit" class="btn primary" :disabled="busy || !canSubmit">确认补录</button>
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
