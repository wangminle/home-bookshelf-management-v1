import { afterEach, describe, expect, it, vi } from 'vitest'

// LOC-19 §12「LWA 路径别名、刷新深链接、照片认证」：BASE_URL 在模块加载时固化，
// 故每个用例 stubEnv 后 resetModules 再动态导入。

afterEach(() => {
  vi.unstubAllEnvs()
  vi.restoreAllMocks()
  vi.resetModules()
  window.history.replaceState({}, '', '/')
})

describe('路径别名部署下的位置域基址', () => {
  it('API 与照片 URL 都带别名前缀，且读取携带 Cookie 凭据', async () => {
    vi.stubEnv('BASE_URL', '/home-bookshelf/')
    vi.resetModules()
    const storage = await import('@/stores/storage')

    expect(storage.shelfPhotoUrl(9)).toBe('/home-bookshelf/api/v1/storage/photos/9/content')

    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(
        new Response(JSON.stringify({ ok: true, data: { items: [], total: 0 } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    await storage.listRooms(false)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url).startsWith('/home-bookshelf/api/v1/storage/rooms')).toBe(true)
    expect((init as RequestInit).credentials).toBe('include')
  })

  it('直连根路径时不带别名前缀', async () => {
    vi.stubEnv('BASE_URL', '/')
    vi.resetModules()
    const storage = await import('@/stores/storage')
    expect(storage.shelfPhotoUrl(9)).toBe('/api/v1/storage/photos/9/content')
  })

  it('刷新深链接：别名下书架详情路由解析到别名前缀且保留 cell 参数', async () => {
    vi.stubEnv('BASE_URL', '/home-bookshelf/')
    window.history.replaceState({}, '', '/home-bookshelf/storage/shelves/3?cell=5')
    vi.resetModules()
    const { default: router } = await import('@/router')

    const resolved = router.resolve('/storage/shelves/3?cell=5')
    expect(resolved.name).toBeTruthy()
    expect(resolved.params.id).toBe('3')
    expect(resolved.query.cell).toBe('5')
    expect(resolved.href).toBe('/home-bookshelf/storage/shelves/3?cell=5')
  })
})
