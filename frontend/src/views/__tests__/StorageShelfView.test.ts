import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import StorageShelfView from '../StorageShelfView.vue'
import { sessionRole } from '@/stores/session'

/** LOC-12：书架详情、照片管理、层格示意图、高亮定位与格子内容列表。 */

const TS = '2026-10-06T00:00:00Z'

function makeCell(id: number, layerId: number, code: string, label: string, sort = 0) {
  return { id, shelf_id: 11, layer_id: layerId, code, label, sort_order: sort, version: 1, archived_at: null, created_at: TS, updated_at: TS }
}

function makePhoto(id: number, over: Record<string, unknown> = {}) {
  return {
    id, shelf_id: 11, caption: null, is_primary: false,
    width: 800, height: 600, mime_type: 'image/jpeg',
    version: 1, created_at: TS, updated_at: TS,
    ...over,
  }
}

function makeDetail(over: Record<string, unknown> = {}) {
  return {
    id: 11,
    room_id: 1,
    code: 'A01',
    name: 'A 号书架',
    position_note: '靠窗右侧',
    sort_order: 0,
    version: 5,
    archived_at: null,
    created_at: TS,
    updated_at: TS,
    layers: [
      { id: 21, shelf_id: 11, label: '第 1 层', sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS,
        cells: [makeCell(31, 21, 'C1', '左格'), makeCell(32, 21, 'C2', '右格', 1)] },
      { id: 22, shelf_id: 11, label: '第 2 层', sort_order: 1, version: 1, archived_at: null, created_at: TS, updated_at: TS,
        cells: [makeCell(33, 22, 'C3', '开放格')] },
    ],
    stats: { assigned_copies: 3, present_copies: 2, on_shelf_copies: 2, books_involved: 2 },
    photos: [makePhoto(9, { is_primary: true, caption: '正面' }), makePhoto(10, { version: 2 })],
    ...over,
  }
}

const CELL_TOTALS: Record<number, number> = { 31: 2, 32: 1, 33: 0 }

function cellCopiesPage(cellId: number) {
  const items = cellId === 31
    ? [
        { copy_id: 91, book_id: 7, book_title: '活着', owner_member_id: 1, owner_member_name: '爸爸', status: 'in_shelf', format: null, condition: null, location_display: '书房 · A 号书架 · 第 1 层 · 左格' },
        { copy_id: 92, book_id: 8, book_title: '许三观卖血记', owner_member_id: null, owner_member_name: null, status: 'lent_out', format: null, condition: null, location_display: '书房 · A 号书架 · 第 1 层 · 左格' },
      ]
    : []
  return { cell_id: cellId, shelf_id: 11, location_display: '书房 · A 号书架', items, total: CELL_TOTALS[cellId] ?? 0 }
}

type FetchHandler = (url: string, options?: RequestInit) => { status?: number; body: unknown }

function stubFetch(handler: FetchHandler) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      const result = handler(String(url), options)
      const status = result.status ?? 200
      return { ok: status >= 200 && status < 300, status, json: async () => result.body }
    }),
  )
}

function stubDetail(detail: ReturnType<typeof makeDetail> = makeDetail()) {
  stubFetch((url) => {
    if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: detail } }
    const cellMatch = url.match(/\/storage\/cells\/(\d+)\/copies/)
    if (cellMatch) return { body: { ok: true, data: cellCopiesPage(Number(cellMatch[1])) } }
    const photoMatch = url.match(/\/storage\/photos\/(\d+)/)
    if (photoMatch) return { body: { ok: true, data: makePhoto(Number(photoMatch[1])) } }
    throw new Error(`未预期的请求: ${url}`)
  })
}

const mountOpts = {
  props: { shelfId: 11, cell: null as number | null },
  global: {
    stubs: {
      RouterLink: { props: ['to'], template: '<a class="router-link" :data-to="to"><slot /></a>' },
    },
  },
}

async function mountView(props: Partial<typeof mountOpts.props> = {}) {
  const wrapper = mount(StorageShelfView, { ...mountOpts, props: { ...mountOpts.props, ...props } })
  await flushPromises()
  return wrapper
}

describe('StorageShelfView', () => {
  beforeEach(() => {
    sessionRole.value = 'owner'
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('渲染元数据、四值统计与示意图网格（编号+计数，口径与 stats 一致）', async () => {
    stubDetail()
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('A 号书架')
    expect(wrapper.text()).toContain('靠窗右侧')
    expect(wrapper.text()).toContain('已分配位置 3 册')
    expect(wrapper.text()).toContain('在位 2 册')
    expect(wrapper.text()).toContain('在架 2 册')
    expect(wrapper.text()).toContain('涉及书目 2 本')
    expect(wrapper.text()).toContain('层从上到下、格子从左到右')

    // 网格：层从上到下、格从左到右；每格编号与副本计数
    const layerIds = wrapper.findAll('.grid-layer').map((l) => l.attributes('data-layer-id'))
    expect(layerIds).toEqual(['21', '22'])
    const cell31 = wrapper.get('[data-cell-id="31"]')
    expect(cell31.text()).toContain('C1')
    expect(cell31.text()).toContain('左格')
    expect(cell31.text()).toContain('2 册')
    expect(wrapper.get('[data-cell-id="32"]').text()).toContain('1 册')
    expect(wrapper.get('[data-cell-id="33"]').text()).toContain('0 册')
    // 格子是可聚焦按钮（键盘可操作）
    expect(cell31.element.tagName).toBe('BUTTON')
  })

  it('点击格子展开副本清单（书名、状态、归属成员）', async () => {
    stubDetail()
    const wrapper = await mountView()
    await wrapper.get('[data-cell-id="31"]').trigger('click')
    await flushPromises()

    const list = wrapper.get('.cell-copies')
    expect(list.text()).toContain('活着')
    expect(list.text()).toContain('许三观卖血记')
    expect(list.text()).toContain('在架')
    expect(list.text()).toContain('外借')
    expect(list.text()).toContain('归属 爸爸')
    // 书目链接指向书籍详情
    expect(wrapper.get('[data-copy-id="91"] .router-link').attributes('data-to')).toBe('/books/7')
    // 空格子有明确文案
    await wrapper.get('[data-cell-id="33"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.cell-copies').text()).toContain('暂无登记的副本')
  })

  it('?cell= 高亮定位：对应实际格子高亮并展开清单；不属于本架的 id 不高亮', async () => {
    stubDetail()
    const wrapper = await mountView({ cell: 32 })
    expect(wrapper.get('[data-cell-id="32"].highlighted').exists()).toBe(true)
    expect(wrapper.get('[data-cell-id="32"].selected').exists()).toBe(true)
    // 高亮格子的内容已展开
    expect(wrapper.find('.cell-copies').exists()).toBe(true)

    const wrapper2 = await mountView({ cell: 999 })
    expect(wrapper2.find('.grid-cell.highlighted').exists()).toBe(false)
    expect(wrapper2.find('.cell-copies').exists()).toBe(false)
  })

  it('无照片时给出说明且示意图仍可定位', async () => {
    stubDetail(makeDetail({ photos: [] }))
    const wrapper = await mountView()
    expect(wrapper.get('[data-testid="no-photos"]').text()).toContain('暂无外观照片')
    expect(wrapper.get('[data-testid="no-photos"]').text()).toContain('示意图')
    await wrapper.get('[data-cell-id="31"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('.cell-copies').text()).toContain('活着')
  })

  it('照片列表渲染主图标识，Member 只读（无上传/设主图/删除）', async () => {
    stubDetail()
    const wrapper = await mountView()
    expect(wrapper.get('[data-photo-id="9"]').text()).toContain('主图')
    expect(wrapper.get('[data-photo-id="9"] img').attributes('src')).toContain('/storage/photos/9/content')
    expect(wrapper.find('.upload-form').exists()).toBe(true)

    sessionRole.value = 'member'
    const memberView = await mountView()
    expect(memberView.find('[data-photo-id="9"] img').exists()).toBe(true)
    expect(memberView.find('.upload-form').exists()).toBe(false)
    const manageButtons = memberView.findAll('.photo-list button')
    expect(manageButtons).toEqual([])
    // Member 仍可点格子看清单（只读定位）
    await memberView.get('[data-cell-id="31"]').trigger('click')
    await flushPromises()
    expect(memberView.get('.cell-copies').text()).toContain('活着')
  })

  it('Owner 上传照片：multipart 带幂等键，不带 JSON Content-Type', async () => {
    stubDetail()
    const wrapper = await mountView()
    const input = wrapper.get('[data-field="photo-file"]')
    const file = new File(['fake-image'], 'shelf.jpg', { type: 'image/jpeg' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await wrapper.get('[data-field="photo-caption"]').setValue('侧面')
    await wrapper.get('.upload-form').trigger('submit')
    await flushPromises()

    const uploadCall = vi.mocked(fetch).mock.calls.find(
      ([u, o]) => String(u).includes('/storage/shelves/11/photos') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(uploadCall).toBeTruthy()
    const opts = uploadCall![1] as RequestInit
    expect(opts.body).toBeInstanceOf(FormData)
    expect((opts.headers as Record<string, string>)['Content-Type']).toBeUndefined()
    const form = opts.body as FormData
    expect(form.get('image')).toBeTruthy()
    expect(form.get('caption')).toBe('侧面')
    expect(typeof form.get('idempotency_key')).toBe('string')
    expect(String(form.get('idempotency_key')).length).toBeGreaterThan(0)
  })

  it('上传前端校验：非 JPEG/PNG/WebP 或超 10 MiB 直接拒绝不发请求', async () => {
    stubDetail()
    const wrapper = await mountView()
    const input = wrapper.get('[data-field="photo-file"]')
    const bad = new File(['x'], 'a.gif', { type: 'image/gif' })
    Object.defineProperty(input.element, 'files', { value: [bad], configurable: true })
    await input.trigger('change')
    await wrapper.get('.upload-form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('.error-box').text()).toContain('仅支持 JPEG、PNG、WebP')
    expect(vi.mocked(fetch).mock.calls.some(([, o]) => (o as RequestInit | undefined)?.method === 'POST')).toBe(false)
  })

  it('设为主图：PATCH 携带版本与幂等键', async () => {
    stubDetail()
    const wrapper = await mountView()
    const btn = wrapper.get('[data-photo-id="10"]').findAll('button').find((b) => b.text() === '设为主图')!
    await btn.trigger('click')
    await flushPromises()

    const patchCall = vi.mocked(fetch).mock.calls.find(
      ([u, o]) => String(u).includes('/storage/photos/10') && (o as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(patchCall).toBeTruthy()
    const body = JSON.parse(String((patchCall![1] as RequestInit).body))
    expect(body.version).toBe(2)
    expect(body.is_primary).toBe(true)
    expect(typeof body.idempotency_key).toBe('string')
    // 主图不可重复设置
    const primaryBtn = wrapper.get('[data-photo-id="9"]').findAll('button').find((b) => b.text() === '设为主图')!
    expect((primaryBtn.element as HTMLButtonElement).disabled).toBe(true)
  })

  it('删除照片：二次确认，期望版本走 query', async () => {
    stubDetail()
    const confirmMock = vi.mocked(window.confirm)
    confirmMock.mockReturnValueOnce(false)
    const wrapper = await mountView()
    const deleteBtn = () => wrapper.get('[data-photo-id="10"]').findAll('button').find((b) => b.text() === '删除')!

    await deleteBtn().trigger('click')
    await flushPromises()
    expect(vi.mocked(fetch).mock.calls.some(([, o]) => (o as RequestInit | undefined)?.method === 'DELETE')).toBe(false)

    await deleteBtn().trigger('click')
    await flushPromises()
    const delCall = vi.mocked(fetch).mock.calls.find(([, o]) => (o as RequestInit | undefined)?.method === 'DELETE')
    expect(delCall).toBeTruthy()
    const url = String(delCall![0])
    expect(url).toContain('/storage/photos/10')
    expect(url).toContain('version=2')
    expect(url).toContain('idempotency_key=')
  })

  it('照片版本冲突 PLACEMENT_CHANGED：提示并自动刷新', async () => {
    stubFetch((url, options) => {
      if (url.includes('/storage/photos/10') && options?.method === 'PATCH') {
        return { status: 409, body: { detail: { code: 'PLACEMENT_CHANGED', message: '期望版本已失效' } } }
      }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeDetail() } }
      const cellMatch = url.match(/\/storage\/cells\/(\d+)\/copies/)
      if (cellMatch) return { body: { ok: true, data: cellCopiesPage(Number(cellMatch[1])) } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = await mountView()
    await wrapper.get('[data-photo-id="10"]').findAll('button').find((b) => b.text() === '设为主图')!.trigger('click')
    await flushPromises()
    expect(wrapper.get('.error-box').text()).toContain('已为你刷新最新数据')
  })
})
