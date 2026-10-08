import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CopyProvisionForm from '@/components/CopyProvisionForm.vue'
import { resetProvisionIdempotencyKeys } from '@/stores/storage'
import { useMembersStore } from '@/stores/members'

/** 补录表单幂等纪律：响应丢失后同一载荷重试必须复用原幂等键（防重复建册）。 */

const MEMBER: { id: number; name: string }[] = [
  { id: 3, name: '爸爸' },
]

function mountForm() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const members = useMembersStore()
  members.members = MEMBER.map((m) => ({
    ...m,
    role: 'member',
    avatar_path: null,
    reading_streak_offset: 0,
    created_at: '2026-10-06T00:00:00Z',
    updated_at: '2026-10-06T00:00:00Z',
  }))
  return mount(CopyProvisionForm, {
    props: { bookId: 7 },
    global: {
      plugins: [pinia],
      stubs: {
        PlacementTargetPicker: {
          template: '<button type="button" data-target @click="$emit(\'update:target\', { shelf_id: 9, cell_id: null })">选书架</button>',
        },
      },
    },
  })
}

function lastProvisionBody(): Record<string, unknown> {
  const call = vi.mocked(fetch).mock.calls.find(([u, o]) =>
    String(u).includes('/storage/copies') && (o as RequestInit | undefined)?.method === 'POST')
  if (!call) throw new Error('未发起补录请求')
  return JSON.parse(String((call[1] as RequestInit).body))
}

function provisionBodies(): Array<Record<string, unknown>> {
  return vi.mocked(fetch).mock.calls
    .filter(([u, o]) => String(u).includes('/storage/copies') && (o as RequestInit | undefined)?.method === 'POST')
    .map(([, o]) => JSON.parse(String((o as RequestInit).body)))
}

async function fillAndSubmit(wrapper: ReturnType<typeof mountForm>) {
  await wrapper.get('[data-field="owner"]').setValue('3')
  await wrapper.get('[data-target]').trigger('click')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

describe('CopyProvisionForm：幂等键复用', () => {
  beforeEach(() => {
    resetProvisionIdempotencyKeys()
    vi.stubGlobal('fetch', vi.fn())
  })

  it('首次提交失败后同一载荷重试复用原幂等键', async () => {
    vi.mocked(fetch).mockImplementation(async () => {
      throw new TypeError('network down')
    })
    const wrapper = mountForm()
    await wrapper.get('[data-field="owner"]').setValue('3')
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('network down')

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const bodies = vi.mocked(fetch).mock.calls
      .filter(([u, o]) => String(u).includes('/storage/copies') && (o as RequestInit | undefined)?.method === 'POST')
      .map(([, o]) => JSON.parse(String((o as RequestInit).body)))
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key)
  })

  it('载荷变化换新键；成功后清空，再次提交重新生成', async () => {
    let ok = false
    vi.mocked(fetch).mockImplementation(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ ok: true, data: { created: 1, items: [] } }),
    }) as unknown as Response)
    const wrapper = mountForm()
    await wrapper.get('[data-field="owner"]').setValue('3')
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    ok = true
    void ok

    await wrapper.get('[data-field="count"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const bodies = vi.mocked(fetch).mock.calls
      .filter(([u, o]) => String(u).includes('/storage/copies') && (o as RequestInit | undefined)?.method === 'POST')
      .map(([, o]) => JSON.parse(String((o as RequestInit).body)))
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.idempotency_key).not.toBe(bodies[0]!.idempotency_key)
  })

  it('结果不明后关闭再打开，相同载荷复用原键', async () => {
    vi.mocked(fetch).mockImplementation(async () => {
      throw new TypeError('response lost')
    })
    const first = mountForm()
    await fillAndSubmit(first)
    first.unmount()

    const second = mountForm()
    await fillAndSubmit(second)
    const bodies = provisionBodies()
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.items).toEqual(bodies[0]!.items)
    expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key)
  })

  it('结果不明的载荷 A→B→回到 A 仍复用 A 的原键', async () => {
    vi.mocked(fetch).mockImplementation(async () => {
      throw new TypeError('response lost')
    })
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    await wrapper.get('[data-field="count"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    await wrapper.get('[data-field="count"]').setValue('1')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const bodies = provisionBodies()
    expect(bodies[0]!.items).toEqual(bodies[2]!.items)
    expect(bodies[0]!.idempotency_key).toBe(bodies[2]!.idempotency_key)
    expect(bodies[1]!.idempotency_key).not.toBe(bodies[0]!.idempotency_key)
  })

  it('HTTP 201 但回执 JSON 截断时保留原键', async () => {
    vi.mocked(fetch).mockImplementation(async () => ({
      ok: true,
      status: 201,
      json: async () => { throw new SyntaxError('unexpected end of json') },
    }) as unknown as Response)
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    expect(wrapper.get('[role="alert"]').text()).toContain('结果未确认')

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const bodies = provisionBodies()
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key)
  })

  it('HTTP 200 但 data 为空时保留原键', async () => {
    vi.mocked(fetch).mockImplementation(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ ok: true }),
    }) as unknown as Response)
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    expect(wrapper.get('[role="alert"]').text()).toContain('结果未确认')

    await wrapper.get('form').trigger('submit')
    await flushPromises()
    const bodies = provisionBodies()
    expect(bodies).toHaveLength(2)
    expect(bodies[1]!.idempotency_key).toBe(bodies[0]!.idempotency_key)
  })
})
