import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import StorageView from '../StorageView.vue'
import StorageRoomForm from '@/components/StorageRoomForm.vue'
import { sessionRole } from '@/stores/session'
import { lastError } from '@/stores/api'

/** LOC-10：/storage 房间/书架清单、建档编辑、归档状态与空态。 */

const TS = '2026-10-06T00:00:00Z'

function makeRoom(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    code: 'study',
    name: '书房',
    description: '二楼靠南',
    sort_order: 0,
    version: 3,
    archived_at: null,
    created_at: TS,
    updated_at: TS,
    stats: { assigned_copies: 5, present_copies: 4, on_shelf_copies: 3, books_involved: 2 },
    ...over,
  }
}

function makeShelf(over: Record<string, unknown> = {}) {
  return {
    id: 11,
    room_id: 1,
    code: 'A01',
    name: 'A 号书架',
    position_note: '靠窗右侧',
    sort_order: 0,
    version: 2,
    archived_at: null,
    created_at: TS,
    updated_at: TS,
    ...over,
  }
}

function makeShelfDetail(over: Record<string, unknown> = {}) {
  return {
    ...makeShelf(),
    layers: [
      {
        id: 21,
        shelf_id: 11,
        label: '第 1 层',
        sort_order: 0,
        version: 1,
        archived_at: null,
        created_at: TS,
        updated_at: TS,
        cells: [
          { id: 31, shelf_id: 11, layer_id: 21, code: 'C1', label: '左格', sort_order: 0, version: 1, archived_at: null, created_at: TS, updated_at: TS },
          { id: 32, shelf_id: 11, layer_id: 21, code: 'C2', label: '右格', sort_order: 1, version: 1, archived_at: null, created_at: TS, updated_at: TS },
        ],
      },
    ],
    stats: { assigned_copies: 5, present_copies: 4, on_shelf_copies: 3, books_involved: 2 },
    photos: [
      { id: 9, shelf_id: 11, caption: null, is_primary: true, width: 800, height: 600, mime_type: 'image/jpeg', version: 1, created_at: TS, updated_at: TS },
    ],
    ...over,
  }
}

type FetchResult = { status?: number; body: unknown }
type FetchHandler = (url: string, options?: RequestInit) => FetchResult | Promise<FetchResult>

function stubFetch(handler: FetchHandler) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      const result = await handler(String(url), options)
      const status = result.status ?? 200
      return {
        ok: status >= 200 && status < 300,
        status,
        json: async () => result.body,
      }
    }),
  )
}

/** 标准有数据场景：1 房间 + 1 书架 + 详情 */
function stubPopulated() {
  stubFetch((url) => {
    if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom()], total: 1 } } }
    if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
    if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
    throw new Error(`未预期的请求: ${url}`)
  })
}

function lastJsonBody(method: string, urlPart: string) {
  const call = vi.mocked(fetch).mock.calls.find(
    ([u, o]) => String(u).includes(urlPart) && (o as RequestInit | undefined)?.method === method,
  )
  if (!call) return null
  return JSON.parse(String((call[1] as RequestInit).body))
}

/** LOC-12：书架卡片经 RouterLink 进详情页；测试环境不挂路由，用 stub 暴露目标地址 */
const mountOpts = {
  global: {
    stubs: {
      RouterLink: { props: ['to'], template: '<a class="router-link" :data-to="to"><slot /></a>' },
    },
  },
}

describe('StorageView', () => {
  beforeEach(() => {
    sessionRole.value = 'owner'
    lastError.value = null
    vi.stubGlobal('confirm', vi.fn(() => true))
  })

  it('按房间分组渲染书架卡片、层格数、统计三值与主图', async () => {
    stubPopulated()
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()

    expect(wrapper.get('[data-room="1"]').text()).toContain('书房')
    expect(wrapper.text()).toContain('二楼靠南')
    // 房间统计三值（§6.3：已分配/在架/涉及书目，不混算）
    expect(wrapper.text()).toContain('已分配位置 5 册')
    expect(wrapper.text()).toContain('在架 3 册')
    expect(wrapper.text()).toContain('涉及书目 2 本')

    const card = wrapper.get('[data-shelf="11"]')
    expect(card.text()).toContain('A 号书架')
    expect(card.text()).toContain('A01')
    expect(card.text()).toContain('靠窗右侧')
    expect(card.text()).toContain('1 层 · 2 格')
    const img = card.get('.shelf-photo img')
    expect(img.attributes('src')).toContain('/storage/photos/9/content')
    // 编号约定固定展示（LOC-01 冻结）
    expect(wrapper.text()).toContain('层从上到下、格子从左到右')
  })

  it('无照片书架显示占位说明', async () => {
    stubFetch((url) => {
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom()], total: 1 } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail({ photos: [] }) } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    expect(wrapper.get('[data-shelf="11"]').text()).toContain('暂无外观照片')
    expect(wrapper.find('[data-shelf="11"] img').exists()).toBe(false)
  })

  it('Owner 空态引导建档', async () => {
    stubFetch((url) => {
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [], total: 0 } } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    const empty = wrapper.get('[data-testid="storage-empty"]')
    expect(empty.text()).toContain('先创建第一个房间')
    expect(empty.find('button').exists()).toBe(true)
  })

  it('Member 空态显示「尚未配置实体书架」且无建档入口', async () => {
    sessionRole.value = 'member'
    stubFetch((url) => {
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [], total: 0 } } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    expect(wrapper.text()).toContain('尚未配置实体书架')
    expect(wrapper.find('[data-testid="storage-empty"] button').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('新建房间')
  })

  it('Member 只读：可见清单与统计，但无任何管理按钮', async () => {
    sessionRole.value = 'member'
    stubPopulated()
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    expect(wrapper.text()).toContain('书房')
    expect(wrapper.text()).toContain('A 号书架')
    expect(wrapper.text()).toContain('已分配位置 5 册')
    expect(wrapper.text()).not.toContain('新建房间')
    expect(wrapper.text()).not.toContain('新建书架')
    expect(wrapper.text()).not.toContain('编辑房间')
    // 没有任何归档/恢复操作按钮（「显示已归档」筛选标签除外）
    const archiveButtons = wrapper.findAll('button').filter((b) => /^(归档|恢复)/.test(b.text()))
    expect(archiveButtons).toEqual([])
    // 读端点之外不产生任何写请求
    const writes = vi.mocked(fetch).mock.calls.filter(([, o]) => {
      const method = (o as RequestInit | undefined)?.method
      return method && method !== 'GET'
    })
    expect(writes).toEqual([])
  })

  it('勾选「显示已归档」后以 include_archived=true 重新加载并展示归档标识', async () => {
    stubFetch((url) => {
      if (url.includes('/storage/rooms')) {
        const includeArchived = url.includes('include_archived=true')
        const items = includeArchived
          ? [makeRoom(), makeRoom({ id: 2, code: 'old', name: '旧储藏室', version: 1, archived_at: TS })]
          : [makeRoom()]
        return { body: { ok: true, data: { items, total: items.length } } }
      }
      if (url.includes('/storage/shelves?room_id=2')) {
        return { body: { ok: true, data: { items: [makeShelf({ id: 12, room_id: 2, code: 'B01', name: '旧书架', version: 1, archived_at: TS })], total: 1 } } }
      }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/12')) return { body: { ok: true, data: makeShelfDetail({ id: 12, room_id: 2, code: 'B01', name: '旧书架', archived_at: TS }) } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    expect(wrapper.text()).not.toContain('旧储藏室')

    await wrapper.get('[data-field="include_archived"]').setValue(true)
    await flushPromises()

    const archivedRoomReq = vi.mocked(fetch).mock.calls.find(([u]) =>
      String(u).includes('/storage/rooms') && String(u).includes('include_archived=true'))
    expect(archivedRoomReq).toBeTruthy()
    expect(wrapper.get('[data-room="2"]').text()).toContain('旧储藏室')
    expect(wrapper.get('[data-room="2"]').text()).toContain('已归档')
    expect(wrapper.get('[data-shelf="12"]').text()).toContain('已归档')
    expect(wrapper.get('[data-shelf="12"].archived').exists()).toBe(true)
    // 已归档房间不提供新建书架入口
    expect(wrapper.get('[data-room="2"]').text()).not.toContain('新建书架')
  })

  it('建档房间：POST 携带幂等键与表单字段', async () => {
    let rooms: ReturnType<typeof makeRoom>[] = []
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms') && options?.method === 'POST') {
        const body = JSON.parse(String(options.body))
        const created = makeRoom({ id: 5, ...body, version: 1 })
        rooms = [created]
        return { status: 201, body: { ok: true, data: created } }
      }
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: rooms, total: rooms.length } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [], total: 0 } } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()

    await wrapper.get('.filter-bar .btn.primary').trigger('click') // 新建房间
    await wrapper.get('[data-field="code"]').setValue('living')
    await wrapper.get('[data-field="name"]').setValue('客厅')
    await wrapper.get('form.storage-form').trigger('submit')
    await flushPromises()

    const body = lastJsonBody('POST', '/storage/rooms')
    expect(body).toBeTruthy()
    expect(typeof body.idempotency_key).toBe('string')
    expect(body.idempotency_key.length).toBeGreaterThan(0)
    expect(body.code).toBe('living')
    expect(body.name).toBe('客厅')
    // 建档后清单刷新可见
    expect(wrapper.text()).toContain('客厅')
    expect(wrapper.text()).toContain('已创建')
  })

  it('编辑房间：PATCH 携带期望版本与幂等键', async () => {
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms/1') && options?.method === 'PATCH') {
        const body = JSON.parse(String(options.body))
        return { body: { ok: true, data: makeRoom({ ...body, version: 4 }) } }
      }
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom()], total: 1 } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()

    const editBtn = wrapper.get('[data-room="1"]').findAll('button').find((b) => b.text() === '编辑房间')!
    await editBtn.trigger('click')
    await wrapper.get('[data-field="name"]').setValue('大书房')
    await wrapper.get('form.storage-form').trigger('submit')
    await flushPromises()

    const body = lastJsonBody('PATCH', '/storage/rooms/1')
    expect(body).toBeTruthy()
    expect(body.version).toBe(3) // 期望版本来自加载时的 version
    expect(body.name).toBe('大书房')
    expect(typeof body.idempotency_key).toBe('string')
  })

  it('归档房间需二次确认；取消确认不发请求', async () => {
    stubPopulated()
    const confirmMock = vi.mocked(window.confirm)
    confirmMock.mockReturnValue(false)

    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    const archiveBtn = wrapper.get('[data-room="1"]').findAll('button').find((b) => b.text() === '归档房间')!
    await archiveBtn.trigger('click')
    await flushPromises()
    expect(lastJsonBody('PATCH', '/storage/rooms/1')).toBeNull()

    confirmMock.mockReturnValue(true)
    await archiveBtn.trigger('click')
    await flushPromises()
    const body = lastJsonBody('PATCH', '/storage/rooms/1')
    expect(body).toBeTruthy()
    expect(body.archived).toBe(true)
    expect(body.version).toBe(3)
    expect(confirmMock).toHaveBeenCalledTimes(2)
  })

  it('建档书架：POST 携带幂等键与逐层格数结构', async () => {
    stubFetch((url, options) => {
      if (url.endsWith('/storage/shelves') && options?.method === 'POST') {
        return { status: 201, body: { ok: true, data: makeShelfDetail({ id: 13 }) } }
      }
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom()], total: 1 } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()

    const createBtn = wrapper.get('[data-room="1"]').findAll('button').find((b) => b.text() === '新建书架')!
    await createBtn.trigger('click')
    await wrapper.get('[data-field="code"]').setValue('A02')
    await wrapper.get('[data-field="name"]').setValue('B 号书架')
    await wrapper.get('[data-field="layer_count"]').setValue('2')
    await wrapper.get('[data-field="cells_0"]').setValue('2')
    await wrapper.get('[data-field="cells_1"]').setValue('1')
    await wrapper.get('form.storage-form').trigger('submit')
    await flushPromises()

    const body = lastJsonBody('POST', '/storage/shelves')
    expect(body).toBeTruthy()
    expect(typeof body.idempotency_key).toBe('string')
    expect(body.room_id).toBe(1)
    expect(body.code).toBe('A02')
    expect(body.layers).toHaveLength(2)
    expect(body.layers[0].cells).toHaveLength(2)
    expect(body.layers[1].cells).toHaveLength(1)
  })

  it('PATCH 409 PLACEMENT_CHANGED：提示刷新并自动重载', async () => {
    let roomLoads = 0
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms/1') && options?.method === 'PATCH') {
        return {
          status: 409,
          body: { detail: { code: 'PLACEMENT_CHANGED', message: 'storage_rooms#1 期望版本 3 已失效' } },
        }
      }
      if (url.includes('/storage/rooms')) {
        roomLoads += 1
        return { body: { ok: true, data: { items: [makeRoom({ version: 4 })], total: 1 } } }
      }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    const loadsBefore = roomLoads

    const editBtn = wrapper.get('[data-room="1"]').findAll('button').find((b) => b.text() === '编辑房间')!
    await editBtn.trigger('click')
    await wrapper.get('[data-field="name"]').setValue('冲突改名')
    await wrapper.get('form.storage-form').trigger('submit')
    await flushPromises()

    expect(lastError.value).toContain('刷新')
    expect(roomLoads).toBeGreaterThan(loadsBefore) // 冲突后自动重载最新数据
  })

  it('书架卡片名称链接到详情页 /storage/shelves/:id（LOC-12）', async () => {
    sessionRole.value = 'owner'
    stubPopulated()
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    const link = wrapper.get('[data-shelf="11"] .shelf-link')
    expect(link.attributes('data-to')).toBe('/storage/shelves/11')
  })

  it('Owner 可从书架卡片进入布局编辑器（LOC-11），Member 无此入口', async () => {
    sessionRole.value = 'owner'
    stubPopulated()
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()

    const layoutBtn = wrapper.get('[data-shelf="11"]').findAll('button').find((b) => b.text() === '布局')
    expect(layoutBtn).toBeTruthy()
    await layoutBtn!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.layout-editor').exists()).toBe(true)
    expect(wrapper.text()).toContain('布局编辑：A 号书架')

    // 关闭编辑器
    await wrapper.get('.layout-editor .actions .btn:not(.primary)').trigger('click')
    expect(wrapper.find('.layout-editor').exists()).toBe(false)
  })

  it('已归档书架不提供布局入口', async () => {
    stubFetch((url) => {
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom()], total: 1 } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf({ archived_at: TS })], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail({ archived_at: TS }) } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    const buttons = wrapper.get('[data-shelf="11"]').findAll('button').map((b) => b.text())
    expect(buttons).not.toContain('布局')
  })

  it('关键字过滤按房间/书架名称筛选', async () => {
    stubPopulated()
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    await wrapper.get('#storage-keyword').setValue('不存在的位置')
    expect(wrapper.text()).toContain('没有匹配')
    await wrapper.get('#storage-keyword').setValue('A 号书架')
    expect(wrapper.text()).toContain('A 号书架')
  })

  it('PLACEMENT_CHANGED 刷新后回填仍打开的编辑表单，重试提交最新版本', async () => {
    let roomVersion = 3
    const patches: Array<Record<string, unknown>> = []
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms') && options?.method === 'PATCH') {
        patches.push(JSON.parse(String(options.body)))
        if (roomVersion === 3) {
          roomVersion = 4 // 服务器侧已被并发推进；本次期望版本失效
          return { status: 409, body: { ok: false, detail: { code: 'PLACEMENT_CHANGED', message: '版本冲突' } } }
        }
        return { body: { ok: true, data: makeRoom({ version: 4 }) } }
      }
      if (url.includes('/storage/rooms')) {
        return { body: { ok: true, data: { items: [makeRoom({ version: roomVersion })], total: 1 } } }
      }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    await wrapper.get('[data-room="1"]').get('button.btn').trigger('click') // 编辑房间（打开 roomForm，version 3）
    expect(wrapper.findComponent(StorageRoomForm).exists()).toBe(true)

    await wrapper.findComponent(StorageRoomForm).vm.$emit('save', {
      code: 'study', name: '书房改', description: null, sort_order: 0,
    })
    await flushPromises()
    expect(patches[0]!['version']).toBe(3) // 第一次用旧版本 → 409
    expect(lastError.value).toContain('已为你刷新最新数据')
    // 刷新后表单仍打开但已回填 version 4 的最新对象
    expect(wrapper.findComponent(StorageRoomForm).exists()).toBe(true)

    await wrapper.findComponent(StorageRoomForm).vm.$emit('save', {
      code: 'study', name: '书房改', description: null, sort_order: 0,
    })
    await flushPromises()
    expect(patches[1]!['version']).toBe(4) // 修复前仍为 3，持续 409
  })

  it('409 刷新未完成时不恢复编辑，晚到回填不覆盖新草稿', async () => {
    let releaseRooms!: (value: { status?: number; body: unknown }) => void
    const roomsPending = new Promise<{ status?: number; body: unknown }>((resolve) => {
      releaseRooms = resolve
    })
    let patchCount = 0
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms') && options?.method === 'PATCH') {
        patchCount += 1
        return { status: 409, body: { ok: false, detail: { code: 'PLACEMENT_CHANGED', message: '版本冲突' } } }
      }
      if (url.includes('/storage/rooms') && patchCount > 0) return roomsPending
      if (url.includes('/storage/rooms')) return { body: { ok: true, data: { items: [makeRoom({ name: '服务端名称', version: 4 })], total: 1 } } }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    await wrapper.get('[data-room="1"]').get('button.btn').trigger('click')
    await wrapper.get('[data-field="name"]').setValue('提交前的名字')
    const pending = wrapper.get('form.storage-form').trigger('submit')
    await flushPromises()

    expect(lastError.value).toContain('正在刷新')
    expect(lastError.value).not.toContain('已为你刷新')
    const nameInput = wrapper.get('[data-field="name"]').element as HTMLInputElement
    expect(nameInput.disabled).toBe(true)
    expect(wrapper.get('form.storage-form button[type="submit"]').attributes('disabled')).toBeDefined()

    releaseRooms({ body: { ok: true, data: { items: [makeRoom({ name: '服务端名称', version: 4 })], total: 1 } } })
    await pending
    await flushPromises()

    expect(lastError.value).toContain('已为你刷新最新数据')
    // 刷新只更新版本，不把用户已输入的名称盖成服务端名称
    expect((wrapper.get('[data-field="name"]').element as HTMLInputElement).value).toBe('提交前的名字')
  })

  it('409 后刷新失败不宣告已刷新，且不能用旧版本再保存', async () => {
    let roomGets = 0
    const patches: Array<Record<string, unknown>> = []
    stubFetch((url, options) => {
      if (url.includes('/storage/rooms') && options?.method === 'PATCH') {
        patches.push(JSON.parse(String(options.body)))
        return { status: 409, body: { ok: false, detail: { code: 'PLACEMENT_CHANGED', message: '版本冲突' } } }
      }
      if (url.includes('/storage/rooms')) {
        roomGets += 1
        if (roomGets === 1) return { body: { ok: true, data: { items: [makeRoom({ version: 1 })], total: 1 } } }
        if (roomGets === 2) throw new TypeError('network down')
        return { body: { ok: true, data: { items: [makeRoom({ version: 4 })], total: 1 } } }
      }
      if (url.includes('/storage/shelves?')) return { body: { ok: true, data: { items: [makeShelf()], total: 1 } } }
      if (url.includes('/storage/shelves/11')) return { body: { ok: true, data: makeShelfDetail() } }
      throw new Error(`未预期的请求: ${url}`)
    })
    const wrapper = mount(StorageView, mountOpts)
    await flushPromises()
    await wrapper.get('[data-room="1"]').get('button.btn').trigger('click')
    await wrapper.findComponent(StorageRoomForm).vm.$emit('save', {
      code: 'study', name: '书房改', description: null, sort_order: 0,
    })
    await flushPromises()

    expect(patches).toHaveLength(1)
    expect(patches[0]!['version']).toBe(1)
    expect(lastError.value).toContain('刷新最新数据失败')
    expect(lastError.value).not.toContain('已为你刷新')
    expect(wrapper.get('form.storage-form button[type="submit"]').attributes('disabled')).toBeDefined()

    await wrapper.findComponent(StorageRoomForm).vm.$emit('save', {
      code: 'study', name: '书房改', description: null, sort_order: 0,
    })
    await flushPromises()
    expect(patches).toHaveLength(1)

    await wrapper.get('[data-field="reload-conflict"]').trigger('click')
    await flushPromises()
    expect(lastError.value).toContain('已为你刷新最新数据')
    await wrapper.findComponent(StorageRoomForm).vm.$emit('save', {
      code: 'study', name: '书房改', description: null, sort_order: 0,
    })
    await flushPromises()
    expect(patches[1]!['version']).toBe(4)
  })
})
