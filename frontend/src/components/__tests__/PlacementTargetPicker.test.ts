import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PlacementTargetPicker from '@/components/PlacementTargetPicker.vue'

/** 级联选择器竞态纪律：快速切换房间/书架时，晚到的旧响应不得覆盖当前选项。 */

const TS = '2026-10-06T00:00:00Z'

function makeRoom(id: number, code: string, name: string) {
  return {
    id, code, name, description: null, sort_order: 0, version: 1,
    archived_at: null, created_at: TS, updated_at: TS,
    stats: { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 },
  }
}

function makeShelf(id: number, name: string) {
  return { id, room_id: 1, code: `S${id}`, name, position_note: null, sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS }
}

function jsonRes(body: unknown, status = 200): Response {
  return { ok: status < 300, status, json: async () => body } as unknown as Response
}

function deferred() {
  let resolve!: (value: Response) => void
  const promise = new Promise<Response>((r) => { resolve = r })
  return { promise, resolve }
}

const deferredShelf101 = deferred()

describe('PlacementTargetPicker：级联请求代际', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('房间 A 的书架响应晚到时不覆盖当前房间 B 的选项', async () => {
    const shelvesA = deferred()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/storage/rooms')) {
        return jsonRes({ ok: true, data: { items: [makeRoom(1, 'A', '房间A'), makeRoom(2, 'B', '房间B')], total: 2 } })
      }
      if (url.includes('room_id=1')) return shelvesA.promise
      if (url.includes('room_id=2')) return jsonRes({ ok: true, data: { items: [makeShelf(101, 'B架')], total: 1 } })
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(PlacementTargetPicker)
    await flushPromises()

    await wrapper.get('[data-field="room"]').setValue(1) // A：响应挂起
    await wrapper.get('[data-field="room"]').setValue(2) // B：立即返回
    await flushPromises()
    expect(wrapper.get('[data-field="shelf"]').text()).toContain('B架')

    shelvesA.resolve(jsonRes({ ok: true, data: { items: [makeShelf(99, 'A架')], total: 1 } })) // A 晚到
    await flushPromises()
    expect(wrapper.get('[data-field="shelf"]').text()).not.toContain('A架')
  })

  it('书架详情响应晚到时不覆盖当前书架的层结构', async () => {
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/storage/rooms')) {
        return jsonRes({ ok: true, data: { items: [makeRoom(1, 'A', '房间A')], total: 1 } })
      }
      if (url.includes('room_id=1')) {
        return jsonRes({ ok: true, data: { items: [makeShelf(101, '一架'), makeShelf(102, '二架')], total: 2 } })
      }
      if (url.includes('/storage/shelves/101')) return deferredShelf101.promise
      if (url.includes('/storage/shelves/102')) {
        return jsonRes({ ok: true, data: { ...makeShelf(102, '二架'), layers: [{ id: 5, shelf_id: 102, label: '二层', sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS, cells: [] }], stats: { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 }, photos: [] } })
      }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(PlacementTargetPicker)
    await flushPromises()
    await wrapper.get('[data-field="room"]').setValue(1)
    await flushPromises()

    await wrapper.get('[data-field="shelf"]').setValue(101) // 详情挂起
    await wrapper.get('[data-field="shelf"]').setValue(102) // 立即返回
    await flushPromises()
    expect(wrapper.get('[data-field="layer"]').text()).toContain('二层')

    deferredShelf101.resolve(jsonRes({ ok: true, data: { ...makeShelf(101, '一架'), layers: [{ id: 4, shelf_id: 101, label: '旧层', sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS, cells: [] }], stats: { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 }, photos: [] } }))
    await flushPromises()
    expect(wrapper.get('[data-field="layer"]').text()).not.toContain('旧层')
  })

  function layerDetail(shelfId: number, label: string) {
    return jsonRes({
      ok: true,
      data: {
        ...makeShelf(shelfId, '架'),
        layers: [{ id: shelfId, shelf_id: shelfId, label, sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS, cells: [] }],
        stats: { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 },
        photos: [],
      },
    })
  }

  it('清空书架后晚到的层格响应不再显示', async () => {
    const detail = deferred()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/storage/rooms')) return jsonRes({ ok: true, data: { items: [makeRoom(1, 'A', '房间A')], total: 1 } })
      if (url.includes('room_id=1')) return jsonRes({ ok: true, data: { items: [makeShelf(101, '一架')], total: 1 } })
      if (url.includes('/storage/shelves/101')) return detail.promise
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(PlacementTargetPicker)
    await flushPromises()
    await wrapper.get('[data-field="room"]').setValue(1)
    await flushPromises()
    await wrapper.get('[data-field="shelf"]').setValue(101)
    await wrapper.get('[data-field="shelf"]').setValue('')
    detail.resolve(layerDetail(101, '不该回来的层'))
    await flushPromises()
    expect(wrapper.find('[data-field="layer"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('不该回来的层')
  })

  it('清空房间后晚到的书架列表不再写回', async () => {
    const shelves = deferred()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/storage/rooms')) return jsonRes({ ok: true, data: { items: [makeRoom(1, 'A', '房间A')], total: 1 } })
      if (url.includes('room_id=1')) return shelves.promise
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(PlacementTargetPicker)
    await flushPromises()
    await wrapper.get('[data-field="room"]').setValue(1)
    await wrapper.get('[data-field="room"]').setValue('')
    shelves.resolve(jsonRes({ ok: true, data: { items: [makeShelf(101, '不该回来的架')], total: 1 } }))
    await flushPromises()
    expect(wrapper.find('[data-field="shelf"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('不该回来的架')
  })

  it('切换房间后晚到的旧架层格不显示到新房间', async () => {
    const detailA = deferred()
    vi.mocked(fetch).mockImplementation(async (input) => {
      const url = String(input)
      if (url.includes('/storage/rooms')) {
        return jsonRes({ ok: true, data: { items: [makeRoom(1, 'A', '房间A'), makeRoom(2, 'B', '房间B')], total: 2 } })
      }
      if (url.includes('room_id=1')) return jsonRes({ ok: true, data: { items: [makeShelf(101, 'A架')], total: 1 } })
      if (url.includes('room_id=2')) return jsonRes({ ok: true, data: { items: [makeShelf(202, 'B架')], total: 1 } })
      if (url.includes('/storage/shelves/101')) return detailA.promise
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(PlacementTargetPicker)
    await flushPromises()
    await wrapper.get('[data-field="room"]').setValue(1)
    await flushPromises()
    await wrapper.get('[data-field="shelf"]').setValue(101)
    await wrapper.get('[data-field="room"]').setValue(2)
    await flushPromises()
    detailA.resolve(layerDetail(101, 'A的旧层'))
    await flushPromises()
    expect(wrapper.text()).not.toContain('A的旧层')
    expect(wrapper.get('[data-field="shelf"]').text()).toContain('B架')
    expect(wrapper.get('[data-field="shelf"]').text()).not.toContain('A架')
  })
})
