<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { sessionRole } from '@/stores/session'
import { lastError, extractApiErrorMessage } from '@/stores/api'

/**
 * Owner 配置多模态模型接口。API Key 只在保存时提交，读回只显示末四位。
 */
const BASE = `${import.meta.env.BASE_URL}api/v1`
const loading = ref(true)
const saving = ref(false)
const notice = ref('')
const keyConfigured = ref(false)
const keyHint = ref<string | null>(null)
const apiKeyInput = ref('')
const clearKey = ref(false)

const form = reactive({
  enabled: false,
  display_name: '识书模型',
  base_url: '',
  model_id: '',
  timeout_seconds: 60,
  max_tokens: 1024,
  temperature: 0,
  image_detail: 'auto' as 'auto' | 'low' | 'high',
})

async function api(path: string, options?: RequestInit) {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  const body = await res.json().catch(() => ({}))
  if (!res.ok || body.ok === false) throw new Error(extractApiErrorMessage(body, res.status, '保存失败'))
  return body.data
}

async function load() {
  loading.value = true
  try {
    const data = await api('/settings/llm')
    form.enabled = data.enabled
    form.display_name = data.display_name
    form.base_url = data.base_url
    form.model_id = data.model_id
    form.timeout_seconds = data.timeout_seconds
    form.max_tokens = data.max_tokens
    form.temperature = data.temperature
    form.image_detail = data.image_detail
    keyConfigured.value = data.api_key_configured
    keyHint.value = data.api_key_hint
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  notice.value = ''
  try {
    const payload: Record<string, unknown> = { ...form }
    if (clearKey.value) payload.api_key = ''
    else if (apiKeyInput.value.trim()) payload.api_key = apiKeyInput.value.trim()
    const data = await api('/settings/llm', { method: 'PUT', body: JSON.stringify(payload) })
    keyConfigured.value = data.api_key_configured
    keyHint.value = data.api_key_hint
    apiKeyInput.value = ''
    clearKey.value = false
    notice.value = '已保存'
  } catch (e: any) {
    lastError.value = e.message
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  if (sessionRole.value === 'owner') load()
  else loading.value = false
})
</script>

<template>
  <section class="llm-settings" v-if="sessionRole === 'owner'">
    <h1>多模态模型</h1>
    <p class="muted">
      填写兼容 OpenAI Chat Completions 的接口。用于看封面图并读出书名、作者。
      API Key 保存在本机数据库，保存后不再回显，只显示末四位。
    </p>

    <p v-if="loading">加载中…</p>
    <form v-else class="settings-form" @submit.prevent="save">
      <label class="check">
        <input v-model="form.enabled" type="checkbox" />
        启用此模型
      </label>

      <label>
        显示名称
        <input v-model="form.display_name" type="text" maxlength="80" required autocomplete="off" />
      </label>

      <label>
        接口地址 Base URL
        <input
          v-model="form.base_url"
          type="url"
          placeholder="https://api.example.com/v1"
          autocomplete="off"
          spellcheck="false"
        />
      </label>

      <label>
        模型 ID
        <input v-model="form.model_id" type="text" maxlength="128" placeholder="gpt-4o" autocomplete="off" spellcheck="false" />
      </label>

      <label>
        API Key
        <input
          v-model="apiKeyInput"
          type="password"
          maxlength="512"
          autocomplete="new-password"
          :placeholder="keyConfigured ? '已配置，留空则不修改' : 'sk-…'"
          :disabled="clearKey"
        />
        <span v-if="keyHint" class="hint">当前 {{ keyHint }}</span>
      </label>

      <label class="check" v-if="keyConfigured">
        <input v-model="clearKey" type="checkbox" />
        清除已保存的 API Key
      </label>

      <div class="row">
        <label>
          超时（秒）
          <input v-model.number="form.timeout_seconds" type="number" min="5" max="180" required />
        </label>
        <label>
          最大输出 token
          <input v-model.number="form.max_tokens" type="number" min="16" max="8192" required />
        </label>
        <label>
          温度
          <input v-model.number="form.temperature" type="number" min="0" max="2" step="0.1" required />
        </label>
        <label>
          识图精细度
          <select v-model="form.image_detail">
            <option value="auto">auto</option>
            <option value="low">low</option>
            <option value="high">high</option>
          </select>
        </label>
      </div>

      <button class="btn" type="submit" :disabled="saving">{{ saving ? '保存中…' : '保存' }}</button>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    </form>
  </section>
  <section v-else class="llm-settings">
    <p>此页面仅 Owner 可用。</p>
  </section>
</template>

<style scoped>
.llm-settings { max-width: 720px; margin: 0 auto; padding: 12px; }
.muted { color: var(--text-muted); font-size: 0.92rem; }
.settings-form { display: flex; flex-direction: column; gap: 14px; margin-top: 16px; }
.settings-form label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--text-muted); }
.settings-form input,
.settings-form select {
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm, 6px);
  font-size: 14px;
  background: var(--card-bg);
  color: var(--text);
  min-height: var(--tap-min, 44px);
  font-family: inherit;
}
.check { flex-direction: row; align-items: center; color: var(--text); font-size: 14px; }
.check input { min-height: auto; width: 18px; height: 18px; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; }
.hint { color: var(--text); font-size: 13px; }
.notice { color: #1c6b48; margin: 0; }
</style>
