import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ShelfLayoutEditor from '@/components/ShelfLayoutEditor.vue'

/** LOC-11：可变层格布局编辑器。PUT /storage/shelves/{id}/layout 全量表达语义。 */

const TS = '2026-10-06T00:00:00Z'

function makeCell(id: number, layerId: number, code: string, label: string, sort = 0) {
  return { id, shelf_id: 11, layer_id: layerId, code, label, sort_order: sort, version: 1, archived_at: null, created_at: TS, updated_at: TS }
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
    stats: { assigned_copies: 0, present_copies: 0, on_shelf_copies: 0, books_involved: 0 },
    photos: [],
    ...over,
  }
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

function stubSuccess(detail: ReturnType<typeof makeDetail> = makeDetail()) {
  stubFetch((url, options) => {
    if (url.includes('/layout') && options?.method === 'PUT') {
      return { body: { ok: true, data: detail } }
    }
    if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: detail } }
    throw new Error(`未预期的请求: ${url}`)
  })
}

function lastPutBody() {
  const call = vi.mocked(fetch).mock.calls.find(
    ([u, o]) => String(u).includes('/layout') && (o as RequestInit | undefined)?.method === 'PUT',
  )
  if (!call) return null
  return JSON.parse(String((call[1] as RequestInit).body))
}

async function mountEditor() {
  const wrapper = mount(ShelfLayoutEditor, { props: { shelfId: 11 } })
  await flushPromises()
  return wrapper
}

describe('ShelfLayoutEditor', () => {
  beforeEach(() => {
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('渲染既有层格、编号预览与固定编号约定', async () => {
    stubSuccess()
    const wrapper = await mountEditor()
    expect(wrapper.text()).toContain('布局编辑：A 号书架（A01）')
    expect(wrapper.text()).toContain('层从上到下、格子从左到右')
    expect(wrapper.get('[data-layer-index="0"]').text()).toContain('第 1 层')
    expect(wrapper.get('[data-layer-index="1"]').text()).toContain('第 2 层')
    // 既有格子显示稳定编号
    const codes = wrapper.findAll('.cell-code').map((c) => c.text())
    expect(codes).toEqual(['C1', 'C2', 'C3'])
    // 各层格数不同（2 格 + 1 格）正常渲染
    expect(wrapper.get('[data-layer-index="0"]').findAll('[data-cell-index]')).toHaveLength(2)
    expect(wrapper.get('[data-layer-index="1"]').findAll('[data-cell-index]')).toHaveLength(1)
  })

  it('保存提交全量表达：既有层格带 id+version，新增不带 id，各层格数可不同', async () => {
    stubSuccess()
    const wrapper = await mountEditor()
    // 第 2 层添加 1 格（新格预览为自动编号）
    await wrapper.get('[data-layer-index="1"] > .btn').trigger('click')
    expect(wrapper.get('[data-layer-index="1"]').text()).toContain('自动编号')
    // 改名第 1 层
    await wrapper.get('[data-layer-index="0"] [data-field="layer-label"]').setValue('顶层')
    await wrapper.findAll('.actions .btn.primary').at(-1)!.trigger('click')
    await flushPromises()

    const body = lastPutBody()
    expect(body).toBeTruthy()
    expect(typeof body.idempotency_key).toBe('string')
    expect(body.idempotency_key.length).toBeGreaterThan(0)
    expect(body.shelf_version).toBe(5)
    expect(body.layers).toHaveLength(2)
    // 改名保稳定 id 与版本
    expect(body.layers[0]).toMatchObject({ id: 21, version: 1, label: '顶层', sort_order: 0 })
    expect(body.layers[0].cells).toEqual([
      { id: 31, version: 1, label: '左格', sort_order: 0 },
      { id: 32, version: 1, label: '右格', sort_order: 1 },
    ])
    // 新增格不带 id
    expect(body.layers[1].id).toBe(22)
    expect(body.layers[1].cells).toHaveLength(2)
    expect(body.layers[1].cells[0]).toMatchObject({ id: 33, version: 1 })
    expect(body.layers[1].cells[1].id).toBeUndefined()
  })

  it('调整层顺序通过 sort_order 表达，id 保持稳定', async () => {
    stubSuccess()
    const wrapper = await mountEditor()
    // 第 1 层下移 → 两层互换位置
    const moveDown = wrapper.get('[data-layer-index="0"] .layer-header .mini-actions')
      .findAll('button').find((b) => b.text() === '下移')!
    await moveDown.trigger('click')
    // 位置序号按当前位置显示；原第 2 层（格 C3）现在排在第一位
    expect(wrapper.get('[data-layer-index="0"]').text()).toContain('C3')
    expect((wrapper.get('[data-layer-index="0"] [data-field="layer-label"]').element as HTMLInputElement).value).toBe('第 2 层')
    await wrapper.findAll('.actions .btn.primary').at(-1)!.trigger('click')
    await flushPromises()

    const body = lastPutBody()
    expect(body.layers[0]).toMatchObject({ id: 22, sort_order: 0 })
    expect(body.layers[1]).toMatchObject({ id: 21, sort_order: 1 })
  })

  it('移除既有格子需确认，提交体中省略（全量表达=归档请求）', async () => {
    stubSuccess()
    const confirmMock = vi.mocked(window.confirm)
    const wrapper = await mountEditor()

    // 取消确认不移除
    confirmMock.mockReturnValueOnce(false)
    const cell1 = wrapper.get('[data-layer-index="0"] [data-cell-index="1"]')
    await cell1.findAll('button').find((b) => b.text() === '移除')!.trigger('click')
    expect(wrapper.get('[data-layer-index="0"]').findAll('[data-cell-index]')).toHaveLength(2)

    // 确认后从清单移除
    await wrapper.get('[data-layer-index="0"] [data-cell-index="1"]')
      .findAll('button').find((b) => b.text() === '移除')!.trigger('click')
    expect(wrapper.get('[data-layer-index="0"]').findAll('[data-cell-index]')).toHaveLength(1)

    await wrapper.findAll('.actions .btn.primary').at(-1)!.trigger('click')
    await flushPromises()
    const body = lastPutBody()
    // C2(id 32) 不出现在提交体中 —— 由后端按全量语义归档（有引用则 409）
    expect(body.layers[0].cells).toEqual([{ id: 31, version: 1, label: '左格', sort_order: 0 }])
  })

  it('LOCATION_IN_USE：指出被引用格子并引导先处理副本，编辑内容保留', async () => {
    stubFetch((url, options) => {
      if (url.includes('/layout') && options?.method === 'PUT') {
        return {
          status: 409,
          body: { detail: { code: 'LOCATION_IN_USE', message: '格子 32 仍被 2 个副本引用（copy_id: 91），请先处理副本' } },
        }
      }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = await mountEditor()
    await wrapper.get('[data-layer-index="0"] [data-cell-index="1"]')
      .findAll('button').find((b) => b.text() === '移除')!.trigger('click')
    await wrapper.findAll('.actions .btn.primary').at(-1)!.trigger('click')
    await flushPromises()

    const errorBox = wrapper.get('.error-box')
    // 翻译为用户可认的位置（层名 + 格编号/名称），不是裸 id
    expect(errorBox.text()).toContain('层「第 1 层」· 格 C2「右格」')
    expect(errorBox.text()).toContain('先把副本移动到其他位置')
    // 不静默删格、编辑状态保留（用户可撤销移除再保存）
    expect(wrapper.get('[data-layer-index="0"]').findAll('[data-cell-index]')).toHaveLength(1)
    expect(wrapper.text()).toContain('保存布局')
  })

  it('PLACEMENT_CHANGED：刷新最新布局并提示重新核对，不自动重放', async () => {
    let detailCalls = 0
    stubFetch((url, options) => {
      if (url.includes('/layout') && options?.method === 'PUT') {
        return {
          status: 409,
          body: { detail: { code: 'PLACEMENT_CHANGED', message: 'storage_shelves#11 期望版本 5 已失效' } },
        }
      }
      if (url.includes('/storage/shelves/11')) {
        detailCalls += 1
        return { body: { ok: true, data: makeDetail({ version: 6 }) } }
      }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = await mountEditor()
    expect(detailCalls).toBe(1)
    await wrapper.get('[data-layer-index="0"] [data-field="layer-label"]').setValue('改名')
    await wrapper.findAll('.actions .btn.primary').at(-1)!.trigger('click')
    await flushPromises()

    expect(wrapper.get('.error-box').text()).toContain('已载入最新版本，请重新核对')
    expect(detailCalls).toBe(2) // 冲突后重新拉取
    expect(vi.mocked(fetch).mock.calls.filter(
      ([u, o]) => String(u).includes('/layout') && (o as RequestInit | undefined)?.method === 'PUT',
    )).toHaveLength(1) // 不自动重放写操作
    // 编辑区已重置为服务端最新（改名丢失，需用户重新确认）
    expect((wrapper.get('[data-layer-index="0"] [data-field="layer-label"]').element as HTMLInputElement).value).toBe('第 1 层')
  })

  it('网络失败后用同一幂等键重试同一载荷', async () => {
    let failOnce = true
    stubFetch((url, options) => {
      if (url.includes('/layout') && options?.method === 'PUT') {
        if (failOnce) {
          failOnce = false
          throw new TypeError('Failed to fetch')
        }
        return { body: { ok: true, data: makeDetail() } }
      }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = await mountEditor()
    const saveBtn = () => wrapper.findAll('.actions .btn.primary').at(-1)!
    await saveBtn().trigger('click')
    await flushPromises()
    expect(wrapper.get('.error-box').text()).toBeTruthy()

    await saveBtn().trigger('click')
    await flushPromises()
    const puts = vi.mocked(fetch).mock.calls.filter(
      ([u, o]) => String(u).includes('/layout') && (o as RequestInit | undefined)?.method === 'PUT',
    )
    expect(puts).toHaveLength(2)
    const key0 = JSON.parse(String((puts[0][1] as RequestInit).body)).idempotency_key
    const key1 = JSON.parse(String((puts[1][1] as RequestInit).body)).idempotency_key
    expect(key0).toBe(key1) // 同 key 同摘要重放，服务端返回原回执不重复建格
    expect(wrapper.text()).toContain('布局已保存')
  })

  it('至少保留 1 层且每层至少 1 格，否则禁止保存', async () => {
    stubSuccess()
    const wrapper = await mountEditor()
    // 移除第 2 层唯一的格子
    await wrapper.get('[data-layer-index="1"] [data-cell-index="0"]')
      .findAll('button').find((b) => b.text() === '移除')!.trigger('click')
    expect(wrapper.text()).toContain('至少保留 1 层，且每层至少 1 格')
    const saveBtn = wrapper.findAll('.actions .btn.primary').at(-1)!
    expect((saveBtn.element as HTMLButtonElement).disabled).toBe(true)
    expect(lastPutBody()).toBeNull()
  })
})
