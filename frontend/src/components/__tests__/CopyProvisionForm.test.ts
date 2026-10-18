import { describe, it, expect, vi, beforeEach } from 'vitest'
import { defineComponent } from 'vue'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import CopyProvisionForm from '@/components/CopyProvisionForm.vue'
import { pendingLocationOperations, prepareLocationOperation, checkLocationOperation, sendLocationOperation } from '@/stores/storage'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { useMembersStore } from '@/stores/members'

// 恢复组件独立测试查询/重放界面；本组通过真实 journal 和 HTTP 验证表单边界。
const RecoveryStub = defineComponent({
  props: ['bookIds'], emits: ['completed', 'changed'],
  setup(_, { emit, expose }) {
    expose({ refresh: vi.fn() })
    async function recover(replay: boolean) {
      const op = pendingLocationOperations()[0]!
      const result = await (replay ? sendLocationOperation(op) : checkLocationOperation(op))
      if (result) emit('completed', result)
      emit('changed')
    }
    return { recover }
  },
  template: `<div data-recovery><button type="button" data-query @click="recover(false)">查询回执</button><button type="button" data-replay @click="recover(true)">重试原操作</button></div>`,
})

function mountForm(bookId = 7) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useMembersStore().members = [{ id: 3, name: '爸爸', role: 'member', avatar_path: null,
    reading_streak_offset: 0, created_at: '', updated_at: '' }]
  return mount(CopyProvisionForm, {
    props: { bookId }, global: { plugins: [pinia], stubs: {
      PlacementTargetPicker: { template: `<button type="button" data-target @click="$emit('update:target', { shelf_id: 9, cell_id: null })">选书架</button>` },
      LocationOperationRecovery: RecoveryStub,
    } },
  })
}
function provisionBodies(): Array<Record<string, unknown>> {
  return vi.mocked(fetch).mock.calls
    .filter(([u, o]) => String(u).endsWith('/storage/copies') && (o as RequestInit | undefined)?.method === 'POST')
    .map(([, o]) => JSON.parse(String((o as RequestInit).body)))
}
async function fillAndSubmit(wrapper: ReturnType<typeof mountForm>) {
  await wrapper.get('[data-field="count"]').setValue('1')
  await wrapper.get('[data-field="owner"]').setValue('3')
  await wrapper.get('[data-target]').trigger('click')
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}
function success(created = 1) {
  return { created, assigned: 0, items: [{ book_id: 7, assigned: [], created: Array.from({ length: created }, (_, i) => i + 10) }] }
}
function respond(data: unknown, status = 201) {
  return { ok: true, status, json: async () => ({ ok: true, data }) } as Response
}

describe('CopyProvisionForm：持久幂等与未知结果恢复', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionRole.value = 'owner'
    sessionMemberId.value = 1
    vi.stubGlobal('fetch', vi.fn())
  })

  it('新增册数默认为0，必须由用户明确填写', async () => {
    const wrapper = mountForm()
    expect((wrapper.get('[data-field="count"]').element as HTMLInputElement).value).toBe('0')
    await wrapper.get('[data-field="owner"]').setValue('3')
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('回执丢失锁住输入和新提交，只能恢复原操作', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('response lost'))
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    expect(wrapper.find('fieldset[disabled]').exists()).toBe(true)
    expect(wrapper.find('[data-recovery]').exists()).toBe(true)
    expect(wrapper.findComponent(RecoveryStub).props('bookIds')).toEqual([7])
    await wrapper.get('form').trigger('submit')
    expect(provisionBodies()).toHaveLength(1)
    vi.mocked(fetch).mockResolvedValue(respond(success()))
    await wrapper.get('[data-replay]').trigger('click')
    await flushPromises()
    const bodies = provisionBodies()
    expect(bodies).toHaveLength(2)
    expect(bodies[1]).toEqual(bodies[0])
    expect(wrapper.emitted('saved')).toEqual([[1]])
  })

  it('重挂后从持久记录恢复，查询回执成功不重发补录', async () => {
    vi.mocked(fetch).mockRejectedValue(new TypeError('response lost'))
    const first = mountForm()
    await fillAndSubmit(first)
    const key = provisionBodies()[0]!.idempotency_key
    first.unmount()
    const second = mountForm()
    await flushPromises()
    expect(second.find('fieldset[disabled]').exists()).toBe(true)
    vi.mocked(fetch).mockResolvedValue(respond({ status: 'completed', idempotency_key: key, result: success() }, 200))
    await second.get('[data-query]').trigger('click')
    await flushPromises()
    expect(provisionBodies()).toHaveLength(1)
    expect(second.emitted('saved')).toEqual([[1]])
    expect(pendingLocationOperations()).toHaveLength(0)
    expect(second.find('fieldset[disabled]').exists()).toBe(false)
  })

  it('恢复多书批次时仅报告当前书目的新增册数', async () => {
    const op = prepareLocationOperation('provision', { items: [
      { book_id: 7, new_copies: { count: 1, owner_member_id: 3, target: { shelf_id: 9 } } },
      { book_id: 8, new_copies: { count: 2, owner_member_id: 3, target: { shelf_id: 9 } } },
    ] }, '多书批次')
    vi.mocked(fetch).mockResolvedValue(respond({ status: 'completed', idempotency_key: op.key,
      result: { created: 3, assigned: 0, items: [
        { book_id: 7, created: [10], assigned: [] }, { book_id: 8, created: [11, 12], assigned: [] },
      ] } }, 200))
    const wrapper = mountForm()
    await wrapper.get('[data-query]').trigger('click'); await flushPromises()
    expect(wrapper.emitted('saved')).toEqual([[1]])
  })

  it('成功确认后才允许用户显式开始下一次补录并生成新键', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(respond(success())).mockResolvedValueOnce(respond(success(2)))
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    await wrapper.get('[data-field="count"]').setValue('2')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(provisionBodies()).toHaveLength(2)
    expect(provisionBodies()[1]!.idempotency_key).not.toBe(provisionBodies()[0]!.idempotency_key)
    expect(wrapper.emitted('saved')).toEqual([[1], [2]])
  })

  it.each(['truncated', 'empty'])('不完整回执 %s 保留原操作并阻止新键', async (kind) => {
    vi.mocked(fetch).mockResolvedValue(kind === 'truncated'
      ? { ok: true, status: 201, json: async () => { throw new SyntaxError('truncated') } } as unknown as Response
      : respond(undefined, 200))
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    expect(pendingLocationOperations()).toHaveLength(1)
    expect(wrapper.find('fieldset[disabled]').exists()).toBe(true)
    await wrapper.get('form').trigger('submit')
    expect(provisionBodies()).toHaveLength(1)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })

  it('本地记录损坏时失败关闭，不能重新补录', async () => {
    localStorage.setItem('bookshelf:location-operations:v1:/:1', 'broken json')
    const wrapper = mountForm()
    await flushPromises()
    expect(wrapper.text()).toContain('无法读取')
    expect(wrapper.find('fieldset[disabled]').exists()).toBe(true)
    expect(fetch).not.toHaveBeenCalled()
  })

  it.each([['member', 1], ['owner', null], ['owner', -1]])('真实会话 %s/%s 不满足Owner身份时禁用提交', async (role, memberId) => {
    sessionRole.value = role as string
    sessionMemberId.value = memberId as number | null
    const wrapper = mountForm()
    await fillAndSubmit(wrapper)
    expect(fetch).not.toHaveBeenCalled()
    expect(wrapper.find('fieldset[disabled]').exists()).toBe(true)
  })
})
