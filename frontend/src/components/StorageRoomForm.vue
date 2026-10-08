<script setup lang="ts">
import { ref, watch } from 'vue'
import type { StorageRoom } from '@/types/models'

/** 房间建档/编辑表单（LOC-10）。仅 Owner 渲染；提交载荷由父组件补版本/幂等键。 */
const props = defineProps<{
  room: StorageRoom | null
  busy: boolean
}>()

const emit = defineEmits<{
  save: [payload: { code: string; name: string; description: string | null; sort_order: number }]
  cancel: []
}>()

const code = ref('')
const name = ref('')
const description = ref('')
const sortOrder = ref(0)

watch(
  () => props.room,
  (room, prev) => {
    // 同一房间的冲突刷新只换版本对象（父组件提交用 props.room.version）。
    // 不把编号/名称等草稿盖成服务端值，否则刷新晚到会清掉用户刚输入的内容（BUG-305）。
    if (room && prev && prev.id === room.id) return
    code.value = room?.code ?? ''
    name.value = room?.name ?? ''
    description.value = room?.description ?? ''
    sortOrder.value = room?.sort_order ?? 0
  },
  { immediate: true },
)

function submit() {
  emit('save', {
    code: code.value.trim(),
    name: name.value.trim(),
    description: description.value.trim() || null,
    sort_order: sortOrder.value,
  })
}
</script>

<template>
  <form class="storage-form" @submit.prevent="submit">
    <h3>{{ room ? `编辑房间「${room.name}」` : '新建房间' }}</h3>
    <label>
      编号（家庭内唯一，建档后保留）
      <input v-model="code" type="text" required maxlength="50" placeholder="如 study" data-field="code" :disabled="busy" />
    </label>
    <label>
      名称
      <input v-model="name" type="text" required maxlength="100" placeholder="如 书房" data-field="name" :disabled="busy" />
    </label>
    <label>
      说明（可选）
      <input v-model="description" type="text" maxlength="200" placeholder="如 二楼靠南的房间" data-field="description" :disabled="busy" />
    </label>
    <label>
      排序（小的在前）
      <input v-model.number="sortOrder" type="number" data-field="sort_order" :disabled="busy" />
    </label>
    <div class="form-actions">
      <button type="submit" class="btn primary" :disabled="busy">{{ room ? '保存修改' : '创建房间' }}</button>
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
.form-actions { display: flex; gap: 8px; }
.btn { padding: 8px 14px; border-radius: 8px; cursor: pointer; background: transparent; border: 1px solid var(--border, #d7dee8); color: inherit; }
.btn.primary { background: var(--primary, #2563eb); border-color: var(--primary, #2563eb); color: #fff; }
.btn:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
