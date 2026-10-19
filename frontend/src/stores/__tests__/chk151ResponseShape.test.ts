import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useApiStore, lastError } from '@/stores/api'
import { listRooms, prepareLocationOperation, pendingLocationOperations, sendLocationOperation, StorageApiError } from '@/stores/storage'
import { sessionRole, sessionMemberId } from '@/stores/session'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  sessionRole.value = 'owner'
  sessionMemberId.value = 1
  lastError.value = null
})
afterEach(() => vi.unstubAllGlobals())

function respond(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), {
    status: 200, headers: { 'Content-Type': 'application/json' },
  })))
}

describe('无效成功响应不能变为裸 TypeError 或完成操作', () => {
  it.each([null, [], 'unexpected', true])('位置接口拒绝无效响应 %j', async (body) => {
    respond(body)
    await expect(listRooms()).rejects.toBeInstanceOf(StorageApiError)
    await expect(listRooms()).rejects.toThrow('结果未确认')
  })
  it('无效写入回执保留原幂等操作', async () => {
    const op = prepareLocationOperation('provision', { items: [] }, '测试')
    respond(null)
    await expect(sendLocationOperation(op)).rejects.toThrow('结果未确认')
    expect(pendingLocationOperations()[0].key).toBe(op.key)
  })
  it('通用接口拒绝 null 并给出可展示的错误', async () => {
    respond(null)
    await expect(useApiStore().get('/books')).rejects.toThrow('响应')
    expect(lastError.value).toContain('响应')
  })
})
