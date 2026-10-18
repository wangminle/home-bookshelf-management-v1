import { beforeEach, describe, expect, it, vi } from 'vitest'
import { sessionMemberId, sessionRole } from '@/stores/session'
import * as storage from '@/stores/storage'

const items = [{ book_id: 7, new_copies: { count: 1, owner_member_id: 3, target: { shelf_id: 9, cell_id: null } } }]
const result = { created: 1, assigned: 0, items: [{ book_id: 7, book_title: '测试书', assigned: [], created: [81] }] }

beforeEach(() => {
  localStorage.clear()
  sessionRole.value = 'owner'
  sessionMemberId.value = 1
  vi.restoreAllMocks()
})

describe('位置写操作持久恢复', () => {
  it('整页重载后相同补录载荷复用原键', async () => {
    const first = storage.reuseProvisionIdempotencyKey(7, items)
    vi.resetModules()
    const freshSession = await import('@/stores/session')
    freshSession.sessionRole.value = 'owner'
    freshSession.sessionMemberId.value = 1
    const fresh = await import('@/stores/storage')
    expect(fresh.reuseProvisionIdempotencyKey(7, items)).toBe(first)
  })

  it('不同登录Owner的相同载荷不会复用对方操作', () => {
    const first = storage.reuseProvisionIdempotencyKey(7, items)
    sessionMemberId.value = 2
    expect(storage.reuseProvisionIdempotencyKey(7, items)).not.toBe(first)
    sessionMemberId.value = 1
    expect(storage.reuseProvisionIdempotencyKey(7, items)).toBe(first)
  })

  it('本地存储不可写时阻止创建新操作', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
    expect(() => storage.reuseProvisionIdempotencyKey(7, items)).toThrow('保存')
  })

  it('恢复到已完成回执时不重发POST', async () => {
    const op = storage.prepareLocationOperation('provision', { items }, '补录测试书')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ ok: true, data: { status: 'completed', idempotency_key: op.key, result } }) })))
    expect(await storage.checkLocationOperation(op)).toEqual(result)
    expect(vi.mocked(fetch).mock.calls).toHaveLength(1)
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBeUndefined()
    expect(storage.pendingLocationOperations()).toHaveLength(0)
  })

  it('回执not_found仍保留原载荷，同键重试成功仅完成原操作', async () => {
    const op = storage.prepareLocationOperation('provision', { items }, '补录测试书')
    vi.stubGlobal('fetch', vi.fn(async (_url, options) => ({ ok: true, status: 200, json: async () => ({ ok: true, data: options?.method === 'POST' ? result : { status: 'not_found', idempotency_key: op.key } }) })))
    expect(await storage.checkLocationOperation(op)).toBeNull()
    expect(storage.pendingLocationOperations()[0].key).toBe(op.key)
    await storage.sendLocationOperation(op)
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))
    expect(body).toEqual({ idempotency_key: op.key, items })
    expect(storage.pendingLocationOperations()).toHaveLength(0)
  })

  it('损坏的成功回执不能清掉未确认操作', async () => {
    const op = storage.prepareLocationOperation('provision', { items }, '补录测试书')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, status: 201, json: async () => ({ ok: true, data: { created: 1, items: [] } }) })))
    await expect(storage.sendLocationOperation(op)).rejects.toThrow('回执')
    expect(storage.pendingLocationOperations()[0].key).toBe(op.key)
  })
})
