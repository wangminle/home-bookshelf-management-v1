<script setup lang="ts">
/**
 * WBS-7：Scope 选择器组件
 * 显示所有可用 Scope，支持勾选，高风险 Scope 标红提示。
 */
import { computed } from 'vue'

const props = defineProps<{
  modelValue: string[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [scopes: string[]]
}>()

interface ScopeOption {
  value: string
  label: string
  risk: 'low' | 'medium' | 'high'
  desc: string
}

const ALL_SCOPES: ScopeOption[] = [
  { value: 'books:read', label: '读取图书', risk: 'low', desc: '查询书架、查看图书详情' },
  { value: 'books:write', label: '写入图书', risk: 'medium', desc: '入库、修改图书信息' },
  { value: 'books:delete', label: '删除图书', risk: 'high', desc: '删除图书记录（不可恢复）' },
  { value: 'reading:read', label: '读取阅读进度', risk: 'low', desc: '查看阅读进度和历史' },
  { value: 'reading:write', label: '写入阅读进度', risk: 'medium', desc: '更新阅读进度、标记完成' },
  { value: 'notes:read', label: '读取笔记', risk: 'low', desc: '查看读书笔记' },
  { value: 'notes:write', label: '写入笔记', risk: 'medium', desc: '创建和修改读书笔记' },
  { value: 'purchases:read', label: '读取购买记录', risk: 'low', desc: '查看购买历史和花费' },
  { value: 'purchases:write', label: '写入购买记录', risk: 'medium', desc: '记录购买信息' },
  { value: 'stats:read', label: '读取个人统计', risk: 'low', desc: '查看个人阅读统计' },
  { value: 'stats:household', label: '家庭统计', risk: 'high', desc: '查看所有成员的统计数据' },
  { value: 'files:read', label: '读取附件', risk: 'low', desc: '下载图书附件和封面' },
  { value: 'members:read', label: '读取成员', risk: 'medium', desc: '查看家庭成员列表' },
]

const selected = computed({
  get: () => new Set(props.modelValue),
  set: (val: Set<string>) => {
    emit('update:modelValue', ALL_SCOPES.filter(s => val.has(s.value)).map(s => s.value))
  },
})

function toggle(scope: string) {
  if (props.disabled) return
  const next = new Set(selected.value)
  if (next.has(scope)) {
    next.delete(scope)
  } else {
    next.add(scope)
  }
  selected.value = next
}

function riskClass(risk: string): string {
  return `risk-${risk}`
}

function riskLabel(risk: string): string {
  const labels: Record<string, string> = { low: '低风险', medium: '中风险', high: '⚠ 高风险' }
  return labels[risk] || risk
}
</script>

<template>
  <div class="scope-selector" role="group" aria-label="权限范围选择">
    <div
      v-for="scope in ALL_SCOPES"
      :key="scope.value"
      class="scope-item"
      :class="[riskClass(scope.risk), { checked: selected.has(scope.value), disabled }]"
    >
      <label>
        <input
          type="checkbox"
          :checked="selected.has(scope.value)"
          :disabled="disabled"
          @change="toggle(scope.value)"
        />
        <span class="scope-label">{{ scope.label }}</span>
        <span class="scope-tag" :class="riskClass(scope.risk)">{{ riskLabel(scope.risk) }}</span>
      </label>
      <p class="scope-desc">{{ scope.desc }}</p>
    </div>
  </div>
</template>

<style scoped>
.scope-selector {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 0.5rem;
}
.scope-item {
  padding: 0.75rem;
  border: 1px solid var(--border-color, #e0e0e0);
  border-radius: 6px;
  transition: border-color 0.15s;
}
.scope-item.checked {
  border-color: var(--primary, #4a90d9);
  background: var(--bg-selected, #f0f7ff);
}
.scope-item.risk-high {
  border-left: 3px solid #e53935;
}
.scope-item.disabled {
  opacity: 0.5;
}
.scope-item label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}
.scope-item.disabled label {
  cursor: not-allowed;
}
.scope-label {
  font-weight: 500;
}
.scope-tag {
  font-size: 0.75rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  margin-left: auto;
}
.scope-tag.risk-low {
  background: #e8f5e9;
  color: #2e7d32;
}
.scope-tag.risk-medium {
  background: #fff3e0;
  color: #e65100;
}
.scope-tag.risk-high {
  background: #ffebee;
  color: #c62828;
  font-weight: 700;
}
.scope-desc {
  margin: 0.25rem 0 0;
  font-size: 0.8rem;
  color: var(--text-secondary, #666);
}
</style>
