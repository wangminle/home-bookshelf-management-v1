import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import CopyPlacementEditor from '@/components/CopyPlacementEditor.vue'
import type { BookCopy } from '@/types/models'

const copy: BookCopy = {
  id: 12, book_id: 8, copy_type: 'physical', status: 'in_shelf',
  format: null, location: null, file_path: null, owner_member_id: null,
  acquire_type: null, condition: null,
  created_at: '2026-10-07T00:00:00Z', updated_at: '2026-10-07T00:00:00Z',
  placement: null, location_display: null,
}

function mountEditor(overrides: Partial<BookCopy> = {}) {
  return mount(CopyPlacementEditor, {
    props: { bookId: 8, copy: { ...copy, ...overrides } },
    global: {
      stubs: {
        PlacementTargetPicker: {
          template: '<button type="button" data-target @click="$emit(\'update:target\', { shelf_id: 9, cell_id: 10 })">选择格子</button>',
        },
      },
    },
  })
}

describe('CopyPlacementEditor：BUG-298 位置版本', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true, status: 200, json: async () => ({ ok: true, data: {} }),
    })))
  })

  it('清除后刷新得到 placement=null，重新设置提交真实版本3', async () => {
    const wrapper = mountEditor({ placement_version: 3 })
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fetch).toHaveBeenCalledTimes(1)
    const [url, options] = vi.mocked(fetch).mock.calls[0]!
    expect(String(url)).toContain('/books/8/copies/12/placement')
    expect(options?.method).toBe('PATCH')
    expect(JSON.parse(String(options?.body))).toEqual({
      placement_version: 3, target: { shelf_id: 9, cell_id: 10 },
      idempotency_key: expect.any(String),
    })
    expect(wrapper.emitted('saved')).toHaveLength(1)
    expect(wrapper.emitted('conflict')).toBeUndefined()
  })

  it('首次未定位的副本使用服务端返回的版本1', async () => {
    const wrapper = mountEditor({ placement_version: 1 })
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]![1]?.body)).placement_version).toBe(1)
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('兼容已定位副本的既有 placement.version 响应', async () => {
    const wrapper = mountEditor({ placement: { shelf_id: 9, cell_id: 10, version: 4 } })
    await wrapper.get('[data-field="mode-clear"]').setValue()
    await wrapper.get('[data-field="keep-legacy"]').setValue()
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0]![1]?.body))).toEqual({
      placement_version: 4, target: null, keep_legacy_location: true,
      idempotency_key: expect.any(String),
    })
  })

  it('缺少位置版本时请求刷新，不猜版本1发送写请求', async () => {
    const wrapper = mountEditor()
    await wrapper.get('[data-target]').trigger('click')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(fetch).not.toHaveBeenCalled()
    expect(wrapper.get('[role="alert"]').text()).toContain('刷新')
    expect(wrapper.emitted('conflict')).toHaveLength(1)
    expect(wrapper.emitted('saved')).toBeUndefined()
  })
})
