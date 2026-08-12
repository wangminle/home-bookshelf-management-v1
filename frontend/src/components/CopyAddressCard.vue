<script setup lang="ts">
/**
 * WBS-3：复制地址卡片组件
 *
 * 展示一行发现面地址，支持一键复制。
 * 从 AgentConnectView 中提取，便于复用和独立测试。
 */
import { ref } from 'vue'

const props = defineProps<{
  label: string
  url: string
  desc?: string
}>()

const copied = ref(false)

async function copyUrl() {
  try {
    await navigator.clipboard.writeText(props.url)
  } catch {
    // Fallback for browsers without clipboard API
    const ta = document.createElement('textarea')
    ta.value = props.url
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
  }
  copied.value = true
  setTimeout(() => { copied.value = false }, 2000)
}
</script>

<template>
  <div class="url-item">
    <div class="url-info">
      <span class="url-label">{{ label }}</span>
      <code class="url-value">{{ url }}</code>
      <span v-if="desc" class="url-desc">{{ desc }}</span>
    </div>
    <button
      @click="copyUrl"
      :class="['btn-copy', { copied }]"
      :aria-label="`复制 ${label} 地址`"
    >
      {{ copied ? '✅ 已复制' : '📋 复制' }}
    </button>
  </div>
</template>

<style scoped>
.url-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.75rem;
  border-bottom: 1px solid var(--border-color, #e0e0e0);
}
.url-item:last-child {
  border-bottom: none;
}
.url-info {
  flex: 1;
}
.url-label {
  font-weight: 600;
  margin-right: 0.5rem;
}
.url-value {
  font-family: monospace;
  font-size: 0.875rem;
  color: var(--primary, #4a90d9);
  word-break: break-all;
}
.url-desc {
  display: block;
  font-size: 0.75rem;
  color: var(--text-secondary, #999);
  margin-top: 0.25rem;
}
.btn-copy {
  padding: 0.4rem 1rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  cursor: pointer;
  background: #f5f5f5;
  white-space: nowrap;
  transition: background 0.15s, border-color 0.15s;
}
.btn-copy:hover {
  background: #eaeaea;
}
.btn-copy.copied {
  background: #e8f5e9;
  border-color: #4caf50;
  color: #2e7d32;
}

@media (max-width: 600px) {
  .url-item {
    flex-direction: column;
    align-items: flex-start;
    gap: 0.5rem;
  }
  .btn-copy {
    align-self: flex-end;
  }
}
</style>
