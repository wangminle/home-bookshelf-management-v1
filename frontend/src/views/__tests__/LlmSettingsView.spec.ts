import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import LlmSettingsView from '../LlmSettingsView.vue'
import { sessionRole } from '@/stores/session'

const saved = {
  enabled: false,
  display_name: '识书模型',
  base_url: '',
  model_id: '',
  api_key_configured: false,
  api_key_hint: null,
  timeout_seconds: 60,
  max_tokens: 1024,
  temperature: 0,
  image_detail: 'auto',
}

describe('LlmSettingsView', () => {
  beforeEach(() => {
    sessionRole.value = 'owner'
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, options?: RequestInit) => {
        if (options?.method === 'PUT') {
          const body = JSON.parse(String(options.body))
          return {
            ok: true,
            status: 200,
            json: async () => ({
              ok: true,
              data: {
                ...saved,
                ...body,
                api_key_configured: Boolean(body.api_key),
                api_key_hint: body.api_key ? '····9911' : null,
              },
            }),
          }
        }
        return { ok: true, status: 200, json: async () => ({ ok: true, data: saved }) }
      }),
    )
  })

  it('留空密钥时不提交 api_key', async () => {
    const wrapper = mount(LlmSettingsView)
    await flushPromises()
    await wrapper.get('input[type="url"]').setValue('https://api.example.com/v1')
    await wrapper.get('input[placeholder="gpt-4o"]').setValue('gpt-4o')
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    const fetchMock = vi.mocked(fetch)
    const put = fetchMock.mock.calls.find((call) => (call[1] as RequestInit | undefined)?.method === 'PUT')
    expect(put).toBeTruthy()
    const body = JSON.parse(String((put![1] as RequestInit).body))
    expect(body.base_url).toBe('https://api.example.com/v1')
    expect(body.model_id).toBe('gpt-4o')
    expect(body).not.toHaveProperty('api_key')
  })

  it('非 Owner 不请求配置', async () => {
    sessionRole.value = 'member'
    const wrapper = mount(LlmSettingsView)
    await flushPromises()
    expect(wrapper.text()).toContain('仅 Owner')
    expect(fetch).not.toHaveBeenCalled()
  })
})
