<script setup lang="ts">
/**
 * WBS-3：能力目录组件
 *
 * 从 Manifest 渲染能力列表，展示每项能力的名称、描述和所需 Scope。
 * 不在前端硬编码能力列表，全部从后端 Manifest 动态渲染。
 */
interface Capability {
  id: string
  name?: string
  description: string
  authorization_required?: boolean
  required_scopes?: string[]
  risk?: string
}

const props = defineProps<{
  capabilities: Capability[]
}>()

function riskClass(risk?: string): string {
  if (!risk) return ''
  return `risk-${risk}`
}

function riskLabel(risk?: string): string {
  const labels: Record<string, string> = {
    read: '只读',
    write: '写入',
    delete: '⚠ 删除',
  }
  return labels[risk] || risk
}
</script>

<template>
  <div class="capability-catalog">
    <div v-if="!capabilities.length" class="empty">
      暂无能力信息
    </div>
    <div
      v-for="cap in capabilities"
      :key="cap.id"
      class="capability-item"
      :class="riskClass(cap.risk)"
    >
      <div class="capability-header">
        <strong>{{ cap.name || cap.id }}</strong>
        <span v-if="cap.risk" class="risk-tag" :class="riskClass(cap.risk)">
          {{ riskLabel(cap.risk) }}
        </span>
      </div>
      <p class="capability-desc">{{ cap.description }}</p>
      <div v-if="cap.required_scopes?.length" class="scopes">
        <span v-for="s in cap.required_scopes" :key="s" class="scope-tag">{{ s }}</span>
      </div>
      <div v-if="cap.authorization_required" class="auth-required">
        🔒 需要授权
      </div>
    </div>
  </div>
</template>

<style scoped>
.capability-catalog {
  display: grid;
  gap: 0.75rem;
}
.empty {
  color: var(--text-secondary, #999);
  text-align: center;
  padding: 1rem;
}
.capability-item {
  padding: 0.75rem;
  border: 1px solid var(--border-color, #e0e0e0);
  border-radius: 6px;
}
.capability-item.risk-write {
  border-left: 3px solid #e65100;
}
.capability-item.risk-delete {
  border-left: 3px solid #c62828;
}
.capability-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.capability-desc {
  font-size: 0.875rem;
  color: var(--text-secondary, #666);
  margin: 0.25rem 0;
}
.scopes {
  margin-top: 0.25rem;
}
.scope-tag {
  display: inline-block;
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  margin: 0.1rem;
  background: #e3f2fd;
  border-radius: 3px;
  font-family: monospace;
}
.risk-tag {
  font-size: 0.7rem;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
}
.risk-tag.risk-read {
  background: #e8f5e9;
  color: #2e7d32;
}
.risk-tag.risk-write {
  background: #fff3e0;
  color: #e65100;
}
.risk-tag.risk-delete {
  background: #ffebee;
  color: #c62828;
  font-weight: 700;
}
.auth-required {
  font-size: 0.75rem;
  color: var(--text-secondary, #999);
  margin-top: 0.25rem;
}
</style>
