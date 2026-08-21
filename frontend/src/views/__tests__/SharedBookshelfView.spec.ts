/**
 * 权限阶段 1：匿名共享书架（C 模式）组件测试。
 *
 * 覆盖：
 * - 可信来源：渲染书目卡片与脱敏详情（仅白名单字段）
 * - LAN_REQUIRED：自动降级为提示 + 登录入口
 * - ANONYMOUS_CATALOG_DISABLED：提示未开启 + 登录入口
 * - 搜索表单触发重新加载
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import SharedBookshelfView from '../SharedBookshelfView.vue'

const BOOK = {
  id: 1,
  title: '三体',
  subtitle: null,
  authors: ['刘慈欣'],
  translators: [],
  publisher: '重庆出版社',
  publish_date: '2008',
  edition: null,
  language: 'zh',
  page_count: 302,
  category: '科幻',
  summary: '简介文本',
  cover_thumbnail_url: '/api/v1/public-catalog/covers/1',
  public_tags: ['科幻'],
  availability_status: 'in_shelf',
}

function okPage(items = [BOOK], total = 1) {
  return { ok: true, data: { items, total, page: 1, page_size: 24, has_more: false } }
}

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/shared', component: SharedBookshelfView },
      { path: '/agent-authorization', component: { template: '<div id="auth" />' } },
    ],
  })
}

async function mountView(fetchImpl: (input: RequestInfo) => Promise<Response>) {
  const fetchMock = vi.fn(fetchImpl)
  vi.stubGlobal('fetch', fetchMock)
  const router = makeRouter()
  router.push('/shared')
  await router.isReady()
  const wrapper = mount(SharedBookshelfView, { global: { plugins: [router] } })
  await flushPromises()
  return { wrapper, fetchMock }
}

const jsonResponse = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }))

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('SharedBookshelfView', () => {
  it('可信来源：渲染书目卡片与脱敏详情', async () => {
    const { wrapper } = await mountView(() =>
      jsonResponse({ ok: true, data: okPage().data }),
    )
    expect(wrapper.text()).toContain('共享书架')
    expect(wrapper.text()).toContain('局域网共享视图')
    expect(wrapper.text()).toContain('三体')
    expect(wrapper.text()).toContain('刘慈欣')
    expect(wrapper.text()).toContain('在架')

    // 打开详情：只有白名单字段，无成员/位置/购买等敏感区
    await wrapper.find('.book-card').trigger('click')
    expect(wrapper.text()).toContain('重庆出版社')
    expect(wrapper.text()).toContain('简介文本')
    expect(wrapper.text()).not.toContain('member')
    expect(wrapper.text()).not.toContain('购买')
  })

  it('LAN_REQUIRED：自动降级为登录入口', async () => {
    const { wrapper } = await mountView(() =>
      jsonResponse({ ok: false, data: null, error: 'LAN_REQUIRED' }, 403),
    )
    expect(wrapper.text()).toContain('仅在可信家庭局域网内开放')
    expect(wrapper.find('.login-link').exists()).toBe(true)
  })

  it('ANONYMOUS_CATALOG_DISABLED：提示未开启并给登录入口', async () => {
    const { wrapper } = await mountView(() =>
      jsonResponse({ ok: false, data: null, error: 'ANONYMOUS_CATALOG_DISABLED' }, 403),
    )
    expect(wrapper.text()).toContain('匿名浏览未开启')
    expect(wrapper.text()).toContain('ANONYMOUS_CATALOG_MODE')
  })

  it('搜索表单触发带参数的重新加载', async () => {
    const { wrapper, fetchMock } = await mountView(() =>
      jsonResponse({ ok: true, data: okPage().data }),
    )
    await wrapper.find('input[aria-label="搜索书目"]').setValue('三体')
    await wrapper.find('form.filters').trigger('submit')
    await flushPromises()
    const lastUrl = String(fetchMock.mock.calls.at(-1)?.[0])
    expect(lastUrl).toContain('query=')
    expect(decodeURIComponent(lastUrl)).toContain('三体')
  })
})
