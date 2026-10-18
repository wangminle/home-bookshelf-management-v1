import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import BookDetailView from '../BookDetailView.vue'
import CopyPlacementEditor from '@/components/CopyPlacementEditor.vue'
import { sessionMemberId, sessionRole } from '@/stores/session'
import { useMembersStore } from '@/stores/members'
import type { BookCopy } from '@/types/models'

function copy(over: Partial<BookCopy> = {}): BookCopy {
  return { id: 11, book_id: 7, copy_type: 'physical', status: 'in_shelf', format: null,
    location: null, file_path: null, owner_member_id: 3, acquire_type: null, condition: null,
    created_at: '', updated_at: '', placement: null, placement_version: 1, location_display: null, ...over }
}
function detail(id = 7, copies = [copy()]) {
  return { id, title: `书目${id}`, cover_path: null, authors: [], tags: [], copies,
    reading_progress: [], purchase_records: [], reading_notes: [], attachments: [], custom_fields: [] }
}
function respond(data: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => ({ ok: status < 400, data, error: status >= 400 ? '刷新不可用' : null }) } as Response
}
async function mountDetail() {
  const pinia = createPinia()
  setActivePinia(pinia)
  useMembersStore().members = [{ id: 3, name: '爸爸', role: 'member', avatar_path: null,
    reading_streak_offset: 0, created_at: '', updated_at: '' }]
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/books/:id', component: BookDetailView, props: true },
    { path: '/storage/intake', component: { template: '<div />' } },
    { path: '/storage/shelves/:id', component: { template: '<div />' } },
  ] })
  await router.push('/books/7#copies')
  await router.isReady()
  const wrapper = mount(BookDetailView, { props: { id: '7' }, global: { plugins: [pinia, router], stubs: {
    PlacementTargetPicker: { template: `<button type="button" data-target @click="$emit('update:target', { shelf_id: 9, cell_id: 10 })">选格子</button>` },
    CopyProvisionForm: { name: 'CopyProvisionForm', props: ['bookId'], template: '<form data-provision><input data-draft /></form>' },
  } } })
  await flushPromises()
  return wrapper
}
function patchBodies() {
  return vi.mocked(fetch).mock.calls.filter(([, o]) => o?.method === 'PATCH')
    .map(([, o]) => JSON.parse(String(o?.body)))
}

describe('BookDetailView：LOC-15 详情副本位置闭环', () => {
  beforeEach(() => {
    sessionRole.value = 'owner'
    sessionMemberId.value = 1
    vi.stubGlobal('fetch', vi.fn())
  })

  it('同书两册独立呈现位置、归属成员、跳转和外借说明', async () => {
    vi.mocked(fetch).mockResolvedValue(respond(detail(7, [
      copy({ placement: { shelf_id: 9, cell_id: 10, version: 2 }, location_display: '书房 / A / 左格' }),
      copy({ id: 12, status: 'lent', placement: { shelf_id: 8, cell_id: null, version: 1 }, location_display: '客厅 / B' }),
    ])))
    const wrapper = await mountDetail()
    expect(wrapper.findAll('.copy-card')).toHaveLength(2)
    expect(wrapper.get('[data-copy-id="11"]').text()).toContain('归属 爸爸')
    expect(wrapper.get('[data-copy-id="11"] a').attributes('href')).toBe('/storage/shelves/9?cell=10')
    expect(wrapper.get('[data-copy-id="12"] a').attributes('href')).toBe('/storage/shelves/8')
    expect(wrapper.get('[data-copy-id="12"]').text()).toContain('副本当前不在该处')
  })

  it('旧文字和无位置清晰分开；Owner 可以转入批量登记', async () => {
    vi.mocked(fetch).mockResolvedValue(respond(detail(7, [copy({ location: '卧室纸箱' }), copy({ id: 12 })])))
    const wrapper = await mountDetail()
    expect(wrapper.get('[data-copy-id="11"]').text()).toContain('文字位置（未关联实体书架）：卧室纸箱')
    expect(wrapper.get('[data-copy-id="12"]').text()).toContain('位置未登记')
    expect(wrapper.get('[data-bulk-provision]').attributes('href')).toBe('/storage/intake?books=7')
  })

  it.each(['member', null])('非Owner %s 不显示任何位置写入入口', async (role) => {
    sessionRole.value = role
    const hidden = copy()
    delete hidden.placement
    delete hidden.placement_version
    delete hidden.location_display
    vi.mocked(fetch).mockResolvedValue(respond(detail(7, [hidden])))
    const wrapper = await mountDetail()
    expect(wrapper.find('.copy-actions').exists()).toBe(false)
    expect(wrapper.find('.provision-entry').exists()).toBe(false)
    expect(wrapper.find('[data-bulk-provision]').exists()).toBe(false)
  })

  it('设置、清除、刷新后重新定位使用最新服务端位置版本', async () => {
    let current = copy()
    vi.mocked(fetch).mockImplementation(async (_, o) => {
      if (o?.method === 'PATCH') {
        const body = JSON.parse(String(o.body))
        current = { ...current, placement_version: current.placement_version! + 1,
          placement: body.target ? { ...body.target, version: current.placement_version! + 1 } : null,
          location_display: body.target ? '书房 / A / 左格' : null }
        return respond({ placement: current.placement })
      }
      return respond(detail(7, [{ ...current }]))
    })
    const wrapper = await mountDetail()
    async function openEditor() {
      await wrapper.get('.copy-actions button').trigger('click')
      return wrapper.getComponent(CopyPlacementEditor)
    }
    let editor = await openEditor()
    await editor.get('[data-target]').trigger('click')
    await editor.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('位置已保存')
    editor = await openEditor()
    await editor.get('[data-field="mode-clear"]').setValue()
    await editor.get('[data-field="clear-legacy"]').setValue()
    await editor.get('form').trigger('submit')
    await flushPromises()
    expect(wrapper.get('[data-copy-id="11"]').text()).toContain('位置未登记')
    editor = await openEditor()
    await editor.get('[data-target]').trigger('click')
    await editor.get('form').trigger('submit')
    await flushPromises()
    expect(patchBodies().map((b) => b.placement_version)).toEqual([1, 2, 3])
    expect(patchBodies()[1]).toMatchObject({ target: null, keep_legacy_location: false })
    expect(wrapper.get('[data-copy-id="11"]').text()).toContain('书房 / A / 左格')
  })

  it('保存成功但详情刷新失败仍明确已保存，重试只重新读取详情', async () => {
    let reads = 0
    vi.mocked(fetch).mockImplementation(async (_, o) => {
      if (o?.method === 'PATCH') return respond({})
      reads++
      return reads === 2 ? respond(null, 503) : respond(detail())
    })
    const wrapper = await mountDetail()
    await wrapper.get('.copy-actions button').trigger('click')
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('.placement-editor').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('位置已保存')
    expect(wrapper.text()).toContain('刷新不可用')
    expect(wrapper.text()).not.toContain('保存失败')
    await wrapper.get('.error-state button').trigger('click')
    await flushPromises()
    expect(patchBodies()).toHaveLength(1)
  })

  it.each(['placement', 'provision'])('切换书目清空旧的 %s 编辑状态', async (mode) => {
    vi.mocked(fetch).mockImplementation(async (u) => respond(String(u).endsWith('/8')
      ? detail(8, [copy({ book_id: 8 })]) : detail()))
    const wrapper = await mountDetail()
    if (mode === 'placement') {
      await wrapper.get('.copy-actions button').trigger('click')
      await wrapper.get('[data-target]').trigger('click')
    } else {
      await wrapper.get('.provision-entry button').trigger('click')
      await wrapper.get('[data-draft]').setValue('旧书草稿')
    }
    await wrapper.setProps({ id: '8' })
    await flushPromises()
    expect(wrapper.text()).toContain('书目8')
    expect(wrapper.find('.placement-editor').exists()).toBe(false)
    expect(wrapper.find('[data-provision]').exists()).toBe(false)
    expect(wrapper.find('.copies-notice').exists()).toBe(false)
  })

  it('补录已成功但详情刷新失败仍保留真实补录册数', async () => {
    let reads = 0
    vi.mocked(fetch).mockImplementation(async () => ++reads === 1 ? respond(detail()) : respond(null, 503))
    const wrapper = await mountDetail()
    await wrapper.get('.provision-entry button').trigger('click')
    wrapper.getComponent({ name: 'CopyProvisionForm' }).vm.$emit('saved', 2)
    await flushPromises()
    expect(wrapper.text()).toContain('已补录 2 册实体副本')
    expect(wrapper.text()).toContain('刷新不可用')
    expect(vi.mocked(fetch).mock.calls.every(([, o]) => !o?.method)).toBe(true)
  })

  it('快速切书时丢弃旧详情响应', async () => {
    let finishOld!: (response: Response) => void
    vi.mocked(fetch).mockImplementation(async (url) => String(url).endsWith('/7')
      ? new Promise<Response>((resolve) => { finishOld = resolve })
      : respond(detail(8, [copy({ book_id: 8 })])))
    const wrapper = await mountDetail()
    await wrapper.setProps({ id: '8' })
    await flushPromises()
    expect(wrapper.text()).toContain('书目8')
    finishOld(respond(detail()))
    await flushPromises()
    expect(wrapper.text()).toContain('书目8')
    expect(wrapper.text()).not.toContain('书目7')
  })

  it('旧书位置保存请求晚返回时不会把成功通知或刷新写入新书', async () => {
    let finishSave!: (response: Response) => void
    vi.mocked(fetch).mockImplementation(async (url, options) => {
      if (options?.method === 'PATCH') return new Promise<Response>((resolve) => { finishSave = resolve })
      return respond(String(url).endsWith('/8') ? detail(8, [copy({ book_id: 8 })]) : detail())
    })
    const wrapper = await mountDetail()
    await wrapper.get('.copy-actions button').trigger('click')
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('.placement-editor').trigger('submit')
    await wrapper.setProps({ id: '8' })
    await flushPromises()
    finishSave(respond({}))
    await flushPromises()
    expect(wrapper.text()).toContain('书目8')
    expect(wrapper.find('.copies-notice').exists()).toBe(false)
    expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/8'))).toHaveLength(1)
  })

})
