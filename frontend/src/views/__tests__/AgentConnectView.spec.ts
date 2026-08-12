/**
 * WBS-3：AgentConnectView 组件测试
 *
 * 测试：
 * - 页面渲染发现面地址列表
 * - 复制功能（成功和降级）
 * - Manifest 加载和展示
 * - 能力目录渲染
 * - 接入流程步骤展示
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import AgentConnectView from '../AgentConnectView.vue'

const mockManifest = {
  schema_version: '1.0',
  service: {
    id: 'home-bookshelf',
    name: '家庭图书管理系统',
    version: '0.2.4',
  },
  links: {
    human_entry: '/agent',
    agent_guide: '/agent/bootstrap.md',
  },
  data_policy: {
    discovery_contains_business_data: false,
    business_access_requires_user_authorization: true,
    authentication: 'Bearer Token',
  },
  capabilities: [
    {
      id: 'books.search',
      name: '搜索图书',
      description: '搜索用户获权范围内的藏书',
      authorization_required: true,
      required_scopes: ['books:read'],
      risk: 'read',
    },
    {
      id: 'books.intake',
      name: '图书入库',
      description: '向家庭书架新增图书',
      authorization_required: true,
      required_scopes: ['books:write'],
      risk: 'write',
    },
  ],
  skills: {
    bundle_version: '0.2.4',
    index: '/agent/skills/index.json',
  },
}

function createTestRouter() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div/>' } },
      { path: '/agent', component: AgentConnectView },
      { path: '/agent-authorization', component: { template: '<div/>' } },
    ],
  })
  return router
}

function mountView() {
  return mount(AgentConnectView, {
    global: {
      plugins: [createTestRouter()],
    },
  })
}

describe('AgentConnectView', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    // Default: clipboard works
    vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
  })

  it('renders the page title', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Agent 连接引导')
  })

  it('renders discovery URL list', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('Manifest')
    expect(text).toContain('Bootstrap')
    expect(text).toContain('API Catalog')
    expect(text).toContain('OpenAPI')
    expect(text).toContain('Skills Index')
    expect(text).toContain('LLMs.txt')
  })

  it('renders manifest details after loading', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('家庭图书管理系统')
    expect(text).toContain('0.2.4')
    expect(text).toContain('不含业务数据')
    expect(text).toContain('Bearer Token')
  })

  it('renders capabilities from manifest', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('搜索图书')
    expect(text).toContain('图书入库')
    expect(text).toContain('books:read')
    expect(text).toContain('books:write')
  })

  it('renders onboarding steps', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const text = wrapper.text()
    expect(text).toContain('接入流程')
    expect(text).toContain('bootstrap.md')
    expect(text).toContain('BOOKSHELF_TOKEN')
  })

  it('shows loading state initially', () => {
    vi.spyOn(global, 'fetch').mockReturnValue(new Promise(() => {}))
    const wrapper = mountView()
    expect(wrapper.text()).toContain('加载中')
  })

  it('shows error on fetch failure', async () => {
    vi.spyOn(global, 'fetch').mockRejectedValue(new Error('Network error'))
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Network error')
  })

  it('shows error on non-200 response', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: false,
      status: 500,
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('HTTP 500')
  })

  it('copies URL on button click', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const copyBtn = wrapper.find('.btn-copy')
    expect(copyBtn.exists()).toBe(true)
    await copyBtn.trigger('click')
    expect(navigator.clipboard.writeText).toHaveBeenCalled()
  })

  it('falls back to execCommand when clipboard fails', async () => {
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('denied'))
    const execSpy = vi.spyOn(document, 'execCommand').mockReturnValue(true)

    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    const copyBtn = wrapper.find('.btn-copy')
    await copyBtn.trigger('click')
    // execCommand fallback should have been called
    expect(execSpy).toHaveBeenCalledWith('copy')
  })

  it('does not hardcode localhost port in URLs', async () => {
    vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockManifest),
    } as Response)
    const wrapper = mountView()
    await flushPromises()
    // URLs should use window.location.origin, not hardcoded port
    const urlValues = wrapper.findAll('.url-value')
    for (const el of urlValues) {
      const url = el.text()
      // URL should start with the origin or base path, not a hardcoded http://127.0.0.1:8000
      if (url.startsWith('http')) {
        expect(url).not.toContain('127.0.0.1:8000')
      }
    }
  })
})
