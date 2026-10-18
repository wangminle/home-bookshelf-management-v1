import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { sessionMemberId, sessionRole } from '@/stores/session'
import StorageIntakeView from '../StorageIntakeView.vue'

const book = (id = 7) => ({ id, title: `书目${id}`, copies: id === 7 ? [{ id: 70, book_id: 7, copy_type: 'physical', owner_member_id: 3, status: 'in_shelf', location: '旧柜', placement_version: 2, placement: null }] : [] })
const empty = { items: [], total: 0 }
const pages = {
  copies_without_placement: { items: [{ copy_id: 70, book_id: 7, book_title: '书目7', status: 'in_shelf', location: null }], total: 21 },
  legacy_location_only: { items: [{ copy_id: 71, book_id: 8, book_title: '旧文字书', status: 'lent_out', location: '旧柜' }], total: 1 },
  books_without_physical_copy: { items: [{ book_id: 8, book_title: '书目8', digital_copies: 1 }], total: 1 },
}
const move = { target: { shelf_id: 9, cell_id: null, shelf_version: 4, cell_version: null, display: '书房 · A架' },
  copies: [{ copy_id: 70, book_id: 7, book_title: '书目7', from: { display: '旧柜' }, to: { display: '书房 · A架' }, placement_version: 2 }], preview_digest: 'hash' }
let handler: ((url: string, options?: RequestInit) => unknown) | undefined
function setupFetch() {
  vi.stubGlobal('fetch', vi.fn(async (url, options) => {
    const u = String(url)
    const value = await handler?.(u, options)
    if (value instanceof Error) throw value
    return { ok: true, status: 200, json: async () => ({ ok: true, data: value ?? (
      u.includes('/unlocated') ? pages : u.endsWith('/members') ? { items: [{ id: 3, name: '家人' }], total: 1 } :
      u.endsWith('/books/7') ? book(7) : u.endsWith('/books/8') ? book(8) : u.endsWith('/placements/preview') ? move : empty) }) }
  }))
}
const options = { global: { plugins: [createPinia()], stubs: {
  RouterLink: { props: ['to'], template: '<a :data-to="JSON.stringify(to)"><slot /></a>' },
  PlacementTargetPicker: { template: '<button type="button" data-pick @click="$emit(\'update:target\', { shelf_id: 9, cell_id: null })">选位置</button>' },
} } }
beforeEach(() => { localStorage.clear(); sessionRole.value = 'owner'; sessionMemberId.value = 1; handler = undefined; setupFetch() })
const calls = (part: string) => vi.mocked(fetch).mock.calls.filter(([u, o]) => String(u).endsWith(part) && o?.method === 'POST')

describe('副本与位置批量登记', () => {
  it('分开呈现三类未定位数据，Member只读', async () => {
    sessionRole.value = 'member'
    const w = mount(StorageIntakeView, options); await flushPromises()
    expect(w.text()).toContain('有副本未定位'); expect(w.text()).toContain('仅有旧位置文字'); expect(w.text()).toContain('尚无实体副本')
    expect(w.find('[data-action="preview-move"]').exists()).toBe(false)
    expect(w.find('input[type="checkbox"]').exists()).toBe(false)
  })

  it('各分类分页独立，切换后回到自己的页码', async () => {
    const w = mount(StorageIntakeView, options); await flushPromises()
    await w.get('[data-action="next"]').trigger('click'); await flushPromises()
    expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toContain('offset=20')
    await w.get('[data-tab="legacy_location_only"]').trigger('click'); await flushPromises()
    expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toContain('offset=0')
    await w.get('[data-tab="copies_without_placement"]').trigger('click'); await flushPromises()
    expect(String(vi.mocked(fetch).mock.calls.at(-1)![0])).toContain('offset=20')
  })

  it('入库带来的book_id去重，读取现有副本且新增数量默认0', async () => {
    const w = mount(StorageIntakeView, { ...options, props: { bookIds: [7, 7, 8] } }); await flushPromises()
    expect(vi.mocked(fetch).mock.calls.filter(([u]) => String(u).endsWith('/books/7'))).toHaveLength(1)
    expect(w.text()).toContain('已有实体副本 1 册')
    expect((w.get('[data-count="7"]').element as HTMLInputElement).value).toBe('0')
    expect(calls('/storage/copies')).toHaveLength(0)
  })

  it('移动先预览再携带服务端版本和摘要提交，不新增副本', async () => {
    handler = (u, o) => u.endsWith('/placements') && o?.method ? { moved: 1, copies: [{ copy_id: 70, book_id: 7, placement_version: 3 }] } : undefined
    const w = mount(StorageIntakeView, options); await flushPromises()
    await w.get('[data-copy="70"]').setValue(true)
    await w.get('[data-pick]').trigger('click')
    await w.get('[data-action="preview-move"]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('旧柜'); expect(w.text()).toContain('书房 · A架')
    expect(calls('/storage/placements')).toHaveLength(0)
    await w.get('[data-action="confirm-move"]').trigger('click'); await flushPromises()
    const body = JSON.parse(String(calls('/storage/placements')[0][1]?.body))
    expect(body).toMatchObject({ preview_digest: 'hash', target: { shelf_id: 9, shelf_version: 4 }, copies: [{ copy_id: 70, placement_version: 2 }] })
    expect(calls('/storage/copies')).toHaveLength(0)
  })

  it('多书补录必须显式数量和归属，预览后才提交', async () => {
    handler = (u, o) => u.endsWith('/storage/copies') && o?.method ? { created: 2, assigned: 0, items: [{ book_id: 8, book_title: '书目8', assigned: [], created: [81, 82] }] } : undefined
    const w = mount(StorageIntakeView, { ...options, props: { bookIds: [7, 8] } }); await flushPromises()
    await w.get('[data-pick]').trigger('click')
    expect(w.get('[data-action="preview-provision"]').attributes('disabled')).toBeDefined()
    await w.get('[data-count="8"]').setValue('2')
    await w.get('[data-owner="8"]').setValue('3')
    await w.get('[data-action="preview-provision"]').trigger('click'); await flushPromises()
    expect(calls('/storage/copies')).toHaveLength(0)
    await w.get('[data-action="confirm-provision"]').trigger('click'); await flushPromises()
    expect(JSON.parse(String(calls('/storage/copies')[0][1]?.body)).items).toEqual([
      { book_id: 8, new_copies: { count: 2, owner_member_id: 3, target: { shelf_id: 9, cell_id: null } } },
    ])
    expect(w.text()).toContain('已补录 2 册')
  })

  it('超过100册或分数册数不允许预览', async () => {
    const w = mount(StorageIntakeView, { ...options, props: { bookIds: [8] } }); await flushPromises()
    await w.get('[data-pick]').trigger('click'); await w.get('[data-owner="8"]').setValue('3')
    for (const value of ['101', '1.5']) {
      await w.get('[data-count="8"]').setValue(value)
      expect(w.get('[data-action="preview-provision"]').attributes('disabled')).toBeDefined()
    }
  })

  it('回执丢失后锁住新提交，重挂展示原操作恢复入口', async () => {
    handler = (u, o) => u.endsWith('/storage/copies') && o?.method ? new TypeError('response lost') : undefined
    const w = mount(StorageIntakeView, { ...options, props: { bookIds: [8] } }); await flushPromises()
    await w.get('[data-pick]').trigger('click'); await w.get('[data-count="8"]').setValue('1'); await w.get('[data-owner="8"]').setValue('3')
    await w.get('[data-action="preview-provision"]').trigger('click'); await w.get('[data-action="confirm-provision"]').trigger('click'); await flushPromises()
    expect(w.text()).toContain('待核对')
    w.unmount()
    const reopened = mount(StorageIntakeView, options); await flushPromises()
    expect(reopened.find('[data-action="replay"]').exists()).toBe(true)
    expect(calls('/storage/copies')).toHaveLength(1)
  })
})
