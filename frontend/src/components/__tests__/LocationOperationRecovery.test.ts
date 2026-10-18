import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { prepareLocationOperation, pendingLocationOperations } from '@/stores/storage'

beforeEach(() => { localStorage.clear(); sessionRole.value = 'owner'; sessionMemberId.value = 1 })
const result = { created: 1, assigned: 0, items: [{ book_id: 7, book_title: '书甲', assigned: [], created: [81] }] }
async function mountRecovery() {
  const mod = await import('../LocationOperationRecovery.vue').catch(() => null)
  expect(mod, '结果未知时需要可用的恢复界面').not.toBeNull()
  return mount(mod!.default)
}

describe('位置操作恢复入口', () => {
  it('查询到完成回执时展示成功且不重发写入', async () => {
    prepareLocationOperation('provision', { items: [{ book_id: 7, new_copies: { count: 1, owner_member_id: 3, target: { shelf_id: 9 } } }] }, '书甲新增1册')
    vi.stubGlobal('fetch', vi.fn(async (url) => ({ ok: true, status: 200, json: async () => ({ ok: true, data: { status: 'completed', idempotency_key: String(url).split('/').at(-1), result } }) })))
    const wrapper = await mountRecovery(); await flushPromises()
    expect(wrapper.text()).toContain('书甲新增1册')
    await wrapper.get('[data-action="check"]').trigger('click'); await flushPromises()
    expect(wrapper.text()).toContain('已确认完成')
    expect(wrapper.emitted('completed')?.[0]).toEqual([result])
    expect(vi.mocked(fetch).mock.calls.every(([, o]) => !o?.method)).toBe(true)
  })

  it('not_found重试先查回执，然后只发送已保存载荷', async () => {
    const op = prepareLocationOperation('provision', { items: [{ book_id: 7, new_copies: { count: 1, owner_member_id: 3, target: { shelf_id: 9 } } }] }, '书甲新增1册')
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => ({ ok: true, status: 200, json: async () => ({ ok: true, data: options?.method ? result : { status: 'not_found', idempotency_key: op.key } }) })))
    const wrapper = await mountRecovery(); await flushPromises()
    await wrapper.get('[data-action="replay"]').trigger('click'); await flushPromises()
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBeUndefined()
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual({ ...op.payload, idempotency_key: op.key })
    expect(pendingLocationOperations()).toHaveLength(0)
  })

  it('失败查询保持记录且不会盲目重发', async () => {
    prepareLocationOperation('provision', { items: [] }, '待核对')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('断网') }))
    const wrapper = await mountRecovery(); await flushPromises()
    await wrapper.get('[data-action="replay"]').trigger('click'); await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(pendingLocationOperations()).toHaveLength(1)
    expect(wrapper.text()).toContain('断网')
  })
})
