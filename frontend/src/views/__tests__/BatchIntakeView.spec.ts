import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import BatchIntakeView from '../BatchIntakeView.vue'
import { sessionRole } from '@/stores/session'
import { lastError } from '@/stores/api'

/** PLN-012 M3（BI-16）：Owner 批量核对工作台。 */

const snapshot = (status = 'in_review') => ({
  id: 1,
  title: '客厅书架',
  status,
  photos: [{ id: 1, photo_id: 'p0001', role: 'cover', image_url: '/intake-workflow/photos/1/image' }],
  candidates: [
    {
      id: 11,
      version: 1,
      status: 'pending_review',
      title: '活着',
      subtitle: null,
      authors: ['余华'],
      isbn: null,
      photo_ids: ['p0001'],
      match_book_id: null,
      match_diff: null,
    },
  ],
  executions: [],
})

function stubFetch(handler: (url: string, options?: RequestInit) => any) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      const result = handler(url, options)
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true, data: result }),
      }
    }),
  )
}

describe('BatchIntakeView', () => {
  beforeEach(() => {
    sessionRole.value = 'owner'
    lastError.value = null
  })

  it('非 Owner 不请求工作台', async () => {
    sessionRole.value = 'member'
    stubFetch(() => ({ items: [] }))
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    expect(wrapper.text()).toContain('仅 Owner')
    expect(fetch).not.toHaveBeenCalled()
  })

  it('加载批次列表并打开工作台', async () => {
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    expect(wrapper.text()).toContain('客厅书架')
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-candidate="11"]').exists()).toBe(true)
    // 候选字段在输入框里（v-model），断言 input value
    expect((wrapper.get('[data-candidate="11"] input[type="text"]').element as HTMLInputElement).value).toBe('活着')
    // 候选照片缩略图走受认证接口
    expect(wrapper.find('img[src*="/intake-workflow/photos/1/image"]').exists()).toBe(true)
  })

  it('确认选中候选调用 confirm 并刷新', async () => {
    stubFetch((url, options) => {
      if (url.endsWith('/confirm')) return { executions: [{ id: 9 }] }
      if (url.includes('/work-items/1')) return snapshot('confirmed')
      return { items: [{ id: 1, title: '客厅书架', status: 'confirmed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true)
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()

    const fetchMock = vi.mocked(fetch)
    const confirmCall = fetchMock.mock.calls.find(
      ([u, o]) => String(u).endsWith('/confirm') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(confirmCall).toBeTruthy()
    expect(JSON.parse(String((confirmCall![1] as RequestInit).body))).toEqual({ candidate_ids: [11] })
    expect(wrapper.text()).toContain('已确认 1 条')
  })

  it('保存候选编辑提交 PATCH 且版本提示可见', async () => {
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        return { ...snapshot().candidates[0], title: '活着（新版）', version: 2 }
      }
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="11"] input[type="text"]').setValue('活着（新版）')
    await wrapper.get('[data-candidate="11"] .cand-actions .btn').trigger('click')
    await flushPromises()

    const fetchMock = vi.mocked(fetch)
    const patch = fetchMock.mock.calls.find(
      ([u, o]) => String(u).endsWith('/candidates/11') && (o as RequestInit | undefined)?.method === 'PATCH',
    )
    expect(patch).toBeTruthy()
    const body = JSON.parse(String((patch![1] as RequestInit).body))
    expect(body.title).toBe('活着（新版）')
    expect(body.authors).toEqual(['余华'])
  })

  it('失败回执展示错误码并提供重试按钮', async () => {
    const snap = {
      ...snapshot('partial'),
      executions: [
        { id: 9, status: 'failed', book_id: null, error_code: 'not_found', error: '目标书目 3 不存在' },
        { id: 10, status: 'completed', book_id: 7, error_code: null, error: null },
      ],
    }
    stubFetch((url, options) => {
      if (String(url).endsWith('/executions/9/retry')) {
        return { id: 9, status: 'completed', book_id: 8, error_code: null, error: null }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'partial' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('not_found')
    await wrapper.get('[data-candidate="11"]').element.scrollIntoView // 存在性
    const retryBtn = wrapper.get('.receipts tbody tr:first-child .btn')
    await retryBtn.trigger('click')
    await flushPromises()
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/executions/9/retry') && (o as RequestInit | undefined)?.method === 'POST')).toBe(true)
  })

  it('匹配预览的冲突差异可见', async () => {
    const snap = snapshot()
    snap.candidates[0].match_book_id = 3
    snap.candidates[0].match_diff = { title: { candidate: '许三观卖血记', existing: '活着' } }
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const match = wrapper.get('.match.conflict')
    expect(match.text()).toContain('#3')
    expect(match.text()).toContain('title')
  })

  /** BUG-249：真实契约下上传返回 { added: [...] }，img 走 BASE + API 相对路径。 */
  async function openWorkbench(wrapper: ReturnType<typeof mount>, uploadResult: { added: unknown[] }) {
    stubFetch((url, options) => {
      if (String(url).endsWith('/photos') && (options as RequestInit | undefined)?.method === 'POST') {
        return uploadResult
      }
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const input = wrapper.get('.upload input[type="file"]')
    const file = new File(['fake-image'], 'cover.jpg', { type: 'image/jpeg' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()
  }

  it('上传照片用 multipart：FormData 请求不带 JSON Content-Type', async () => {
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await openWorkbench(wrapper, {
      added: [{ id: 2, photo_id: 'p0002', role: 'cover', image_url: '/intake-workflow/photos/2/image' }],
    })

    const fetchMock = vi.mocked(fetch)
    const uploadCall = fetchMock.mock.calls.find(
      ([u, o]) => String(u).endsWith('/photos') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(uploadCall).toBeTruthy()
    const opts = uploadCall![1] as RequestInit
    expect(opts.body).toBeInstanceOf(FormData)
    // 浏览器必须自己生成带 boundary 的 multipart 头；显式 JSON 头会导致后端解析不到文件
    expect((opts.headers as Record<string, string> | undefined)?.['Content-Type']).toBeUndefined()
    // JSON 请求仍保持 JSON Content-Type
    const confirmCall = fetchMock.mock.calls.find(([u]) => String(u).includes('/work-items'))
    expect((confirmCall![1] as RequestInit).headers).toMatchObject({ 'Content-Type': 'application/json' })
    expect(wrapper.text()).toContain('已上传 1 张照片')
  })

  it('上传返回 added 为空时提示错误而不是成功', async () => {
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await openWorkbench(wrapper, { added: [] })

    expect(lastError.value).toBe('照片上传未保存任何文件，请重试')
    expect(wrapper.text()).not.toContain('已上传')
  })

  it('照片缩略图按真实契约拼接 image_url（无 /api/v1 重复前缀）', async () => {
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const src = wrapper.get('.photos img').attributes('src')
    expect(src).toContain('/intake-workflow/photos/1/image')
    expect(src).not.toContain('/api/v1/api/v1')
  })

  /** BUG-269：第二轮上传的 photo_ids 必须从已有最大序号续起，不能与已有照片冲突。 */
  it('第二轮上传的 photo_ids 从已有最大序号续起', async () => {
    const snap = snapshot()
    snap.photos = [
      { id: 1, photo_id: 'p0001', role: 'cover', image_url: '/intake-workflow/photos/1/image' },
      { id: 2, photo_id: 'p0002', role: 'cover', image_url: '/intake-workflow/photos/2/image' },
    ]
    stubFetch((url, options) => {
      if (String(url).endsWith('/photos') && (options as RequestInit | undefined)?.method === 'POST') {
        return { added: [{ id: 3, photo_id: 'p0003', role: 'cover', image_url: '/intake-workflow/photos/3/image' }] }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const input = wrapper.get('.upload input[type="file"]')
    const file = new File(['fake-image'], 'cover.jpg', { type: 'image/jpeg' })
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushPromises()

    const uploadCall = vi.mocked(fetch).mock.calls.find(
      ([u, o]) => String(u).endsWith('/photos') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(uploadCall).toBeTruthy()
    const form = (uploadCall![1] as RequestInit).body as FormData
    expect(form.get('photo_ids')).toBe('p0003')
  })

  /** BUG-273：服务端内容没变时保留人工编辑；重新识别出新值后输入框必须刷新。 */
  async function editThenReload(
    wrapper: ReturnType<typeof mount>,
    reloadedCandidate: Record<string, unknown>,
  ) {
    let candidate: Record<string, unknown> = { ...snapshot().candidates[0] }
    stubFetch((url, options) => {
      if (String(url).endsWith('/preview-matches') && (options as RequestInit | undefined)?.method === 'POST') {
        candidate = reloadedCandidate
        return { matched: 0 }
      }
      if (url.includes('/work-items/1')) return { ...snapshot(), candidates: [candidate] }
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="11"] [data-field="title"]').setValue('活着（人工草稿）')
    const reloadBtn = wrapper.findAll('.actions .btn').find((b) => b.text().includes('匹配已有书'))!
    await reloadBtn.trigger('click') // 匹配已有书 → 重新加载快照
    await flushPromises()
  }

  it('候选服务端内容未变时，重新加载保留人工编辑', async () => {
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await editThenReload(wrapper, { ...snapshot().candidates[0] })
    expect((wrapper.get('[data-candidate="11"] [data-field="title"]').element as HTMLInputElement).value)
      .toBe('活着（人工草稿）')
  })

  it('候选服务端内容变化（重新识别）后，输入框刷新为新值', async () => {
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    const reloaded = {
      ...snapshot().candidates[0],
      title: '许三观卖血记',
      authors: ['余华'],
      version: 2,
    }
    await editThenReload(wrapper, reloaded)
    expect((wrapper.get('[data-candidate="11"] [data-field="title"]').element as HTMLInputElement).value)
      .toBe('许三观卖血记')
  })

  /** BUG-275：副标题参与人工核对，且 PATCH 提交 subtitle。 */
  it('副标题展示、可编辑，保存时 PATCH 携带 subtitle', async () => {
    const snap = snapshot()
    snap.candidates[0].subtitle = '修订纪念版'
    let saved: Record<string, unknown> | null = null
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        saved = JSON.parse(String((options as RequestInit).body))
        return { ...snap.candidates[0], subtitle: '新版副题', version: 2 }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const subtitleInput = wrapper.get('[data-candidate="11"] [data-field="subtitle"]')
    expect((subtitleInput.element as HTMLInputElement).value).toBe('修订纪念版')
    await subtitleInput.setValue('新版副题')
    await wrapper.get('[data-candidate="11"] .cand-actions .btn').trigger('click')
    await flushPromises()
    expect(saved).toBeTruthy()
    expect((saved as unknown as Record<string, unknown>).subtitle).toBe('新版副题')
  })

  it('执行回执状态映射为中文标签', async () => {
    const snap = {
      ...snapshot('confirmed'),
      executions: [
        { id: 9, status: 'partial', book_id: 7, error_code: null, error: null },
      ],
    }
    stubFetch((url, options) => {
      if (String(url).endsWith('/execute') && (options as RequestInit | undefined)?.method === 'POST') {
        return { status: 'partial' }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'confirmed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('.actions .btn.primary:not([disabled])').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('执行完成：部分完成')
    expect(wrapper.get('.receipts tbody td:nth-child(2)').text()).toBe('部分完成')
  })

  /** BUG-281：输入框有未保存修改时直接确认——必须先保存（PATCH）再确认，
   * 不得出现"页面显示新值、实际确认服务端旧值"的成功假象。 */
  it('未保存修改的候选在确认前先自动保存（PATCH 先于 confirm）', async () => {
    const patches: Record<string, unknown>[] = []
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        patches.push(JSON.parse(String((options as RequestInit).body)))
        return { ...snapshot().candidates[0], title: '许三观卖血记', subtitle: '正确副题', version: 2 }
      }
      if (String(url).endsWith('/confirm')) return { executions: [{ id: 9 }] }
      if (url.includes('/work-items/1')) return snapshot('confirmed')
      return { items: [{ id: 1, title: '客厅书架', status: 'confirmed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="11"] [data-field="title"]').setValue('许三观卖血记')
    await wrapper.get('[data-candidate="11"] [data-field="subtitle"]').setValue('正确副题')
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true)
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()

    const fetchMock = vi.mocked(fetch)
    const patchIndex = fetchMock.mock.calls.findIndex(
      ([u, o]) => String(u).endsWith('/candidates/11') && (o as RequestInit | undefined)?.method === 'PATCH',
    )
    const confirmIndex = fetchMock.mock.calls.findIndex(
      ([u, o]) => String(u).endsWith('/confirm') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(patchIndex).toBeGreaterThanOrEqual(0)
    expect(confirmIndex).toBeGreaterThan(patchIndex) // 先保存后确认
    expect(patches[0]).toMatchObject({ title: '许三观卖血记', subtitle: '正确副题' })
    expect(wrapper.text()).toContain('已保存 1 条修改并确认 1 条')
  })

  it('无未保存修改时确认不产生多余 PATCH（分隔符写法等价不算脏）', async () => {
    let patchCount = 0
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        patchCount++
        return snapshot().candidates[0]
      }
      if (String(url).endsWith('/confirm')) return { executions: [{ id: 9 }] }
      if (url.includes('/work-items/1')) return snapshot('confirmed')
      return { items: [{ id: 1, title: '客厅书架', status: 'confirmed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    // 作者分隔符从顿号改成逗号——语义等价，不应触发保存
    await wrapper.get('[data-candidate="11"] [data-field="authors"]').setValue('余华，')
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true)
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()
    expect(patchCount).toBe(0)
    expect(wrapper.text()).toContain('已确认 1 条')
  })

  /** BUG-282：重建后软删除（rejected）候选仅供追溯——不可勾选、不可编辑。 */
  it('rejected 候选禁用勾选与编辑并标注追溯', async () => {
    const snap = snapshot()
    snap.candidates[0].status = 'rejected'
    snap.candidates[0].title = '过时识别结果'
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const box = wrapper.get('[data-candidate="11"] input[type="checkbox"]')
    expect((box.element as HTMLInputElement).disabled).toBe(true)
    expect((wrapper.get('[data-candidate="11"] [data-field="title"]').element as HTMLInputElement).disabled).toBe(true)
    expect(wrapper.get('[data-candidate="11"].rejected').exists()).toBe(true)
    expect(wrapper.text()).toContain('已被重建取代，仅供追溯')
    // 勾选按钮全部禁用 → 确认按钮不可用
    expect(wrapper.get('.actions .btn.primary').attributes('disabled')).toBeDefined()
  })

  /** 缺陷1：进程中断后任务停在 executing，页面须提供「恢复执行」入口（同一 execute 接口）。 */
  it('executing 任务提供恢复执行按钮并调用同一 execute 接口', async () => {
    stubFetch((url, options) => {
      if (String(url).endsWith('/execute') && (options as RequestInit | undefined)?.method === 'POST') {
        return { status: 'partial' }
      }
      if (url.includes('/work-items/1')) return snapshot('executing')
      return { items: [{ id: 1, title: '客厅书架', status: 'executing' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const resumeBtn = wrapper.findAll('.actions .btn').find((b) => b.text().includes('恢复执行'))
    expect(resumeBtn).toBeTruthy()
    expect((resumeBtn!.element as HTMLButtonElement).disabled).toBe(false)
    await resumeBtn!.trigger('click')
    await flushPromises()
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/execute') && (o as RequestInit | undefined)?.method === 'POST')).toBe(true)
    expect(wrapper.text()).toContain('执行完成：部分完成')
  })

  /** BUG-289 纵深防御：任务被后端算成 failed 但回执中仍有 executing 命令（租约未过期）时，
   * 恢复执行入口不能消失，仍走同一 execute 接口；无 executing 命令的 failed 任务则走既有重试按钮。 */
  it('failed 任务但存在 executing 命令时仍显示恢复执行并调用 execute 接口', async () => {
    const snap = {
      ...snapshot('failed'),
      executions: [{ id: 9, status: 'executing', book_id: null, error_code: null, error: null }],
    }
    stubFetch((url, options) => {
      if (String(url).endsWith('/execute') && (options as RequestInit | undefined)?.method === 'POST') {
        return { status: 'partial' }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'failed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const resumeBtn = wrapper.findAll('.actions .btn').find((b) => b.text().includes('恢复执行'))
    expect(resumeBtn).toBeTruthy()
    expect((resumeBtn!.element as HTMLButtonElement).disabled).toBe(false)
    await resumeBtn!.trigger('click')
    await flushPromises()
    const fetchMock = vi.mocked(fetch)
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/execute') && (o as RequestInit | undefined)?.method === 'POST')).toBe(true)
    expect(wrapper.text()).toContain('执行完成：部分完成')
  })

  /** 缺陷1补充：failed 且无 executing 命令时不显示恢复按钮，回执行内重试入口不受影响。 */
  it('failed 且无 executing 命令时不显示恢复按钮', async () => {
    const snap = {
      ...snapshot('failed'),
      executions: [{ id: 9, status: 'failed', book_id: null, error_code: 'not_found', error: '目标书目不存在' }],
    }
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'failed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('恢复执行')
    expect(wrapper.get('.receipts tbody tr .btn').text()).toContain('重试')
  })

  /** 缺陷1补充：confirmed 任务不出现恢复按钮，只显示执行入库。 */
  it('confirmed 任务显示执行入库而非恢复按钮', async () => {
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snapshot('confirmed')
      return { items: [{ id: 1, title: '客厅书架', status: 'confirmed' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('执行入库')
    expect(wrapper.text()).not.toContain('恢复执行')
  })

  /** 缺陷2：候选含 ISBN 归属冲突时展示冲突信息；勾选「强制关联此书」后 confirm 携带 resolutions。 */
  it('冲突候选展示冲突提示，勾选后 confirm 请求体含 force_link resolutions', async () => {
    const snap = snapshot()
    snap.candidates[0].conflicts = {
      type: 'isbn_ownership',
      message: 'ISBN 9787506365437 已归属《许三观卖血记》(#3)，与候选《活着》不一致',
      resolved: null,
    }
    let confirmBody: Record<string, unknown> | null = null
    stubFetch((url, options) => {
      if (String(url).endsWith('/confirm') && (options as RequestInit | undefined)?.method === 'POST') {
        confirmBody = JSON.parse(String((options as RequestInit).body))
        return { executions: [{ id: 9 }] }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    // 冲突提示醒目展示
    const warn = wrapper.get('[data-candidate="11"] .conflict-warn')
    expect(warn.text()).toContain('ISBN 9787506365437')
    expect(warn.text()).toContain('强制关联此书')
    // 默认不勾选：确认不带 resolutions
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true)
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()
    expect(confirmBody).toEqual({ candidate_ids: [11] })
  })

  it('勾选「强制关联此书」后 confirm 请求体携带该候选的 force_link', async () => {
    const snap = snapshot()
    snap.candidates[0].conflicts = { message: 'ISBN 归属冲突：候选与既有书 #3 书名/作者不一致' }
    let confirmBody: Record<string, unknown> | null = null
    stubFetch((url, options) => {
      if (String(url).endsWith('/confirm') && (options as RequestInit | undefined)?.method === 'POST') {
        confirmBody = JSON.parse(String((options as RequestInit).body))
        return { executions: [{ id: 9 }] }
      }
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true) // 选中候选
    await wrapper.get('.conflict-warn input[type="checkbox"]').setValue(true) // 勾选强制关联
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()
    expect(confirmBody).toEqual({ candidate_ids: [11], resolutions: { 11: 'force_link' } })
  })

  /** 缺陷3：照片逐条 warnings（识别失败/字段异常/ISBN 校验失败等）须可见。 */
  it('照片 warnings 以列表展示且视觉可区分，无警告不渲染警告区', async () => {
    const snap = snapshot()
    snap.photos = [
      { id: 1, photo_id: 'p0001', role: 'cover', image_url: '/intake-workflow/photos/1/image',
        warnings: [{ code: 'recognition_failed', message: '识别服务超时' }] },
      { id: 2, photo_id: 'p0002', role: 'back', image_url: '/intake-workflow/photos/2/image', warnings: [] },
    ]
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const warnList = wrapper.get('[data-warnings="p0001"]')
    expect(warnList.text()).toContain('recognition_failed')
    expect(warnList.text()).toContain('识别服务超时')
    // 有警告的照片视觉区分（warned class），无警告的不渲染警告列表
    expect(wrapper.get('[data-photo-id="p0001"].warned').exists()).toBe(true)
    expect(wrapper.find('[data-warnings="p0002"]').exists()).toBe(false)
    expect(wrapper.find('[data-photo-id="p0002"].warned').exists()).toBe(false)
  })

  /** 缺陷4：executed 候选即将被服务端拒绝拆分，前端同步禁用拆分按钮。 */
  it('executed 候选禁用拆分照片按钮', async () => {
    const snap = snapshot()
    snap.candidates[0].status = 'executed'
    snap.candidates[0].photo_ids = ['p0001', 'p0002']
    stubFetch((url) => {
      if (url.includes('/work-items/1')) return snap
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    const splitBtn = wrapper.get('[data-candidate="11"] .cand-actions .btn:nth-child(2)')
    expect(splitBtn.text()).toContain('拆分照片')
    expect((splitBtn.element as HTMLButtonElement).disabled).toBe(true)
  })

  /** BUG-290：勾选「强制关联」后修改字段的确认流程三段。 */
  function conflictedSnapshot() {
    const snap = snapshot()
    snap.candidates[0] = {
      ...snap.candidates[0],
      match_book_id: 3,
      match_diff: { title: { candidate: '活着', existing: '许三观卖血记' } },
      conflicts: { code: 'isbn_ownership_conflict', message: 'ISBN 已绑定《许三观卖血记》', book_id: 3, existing_title: '许三观卖血记' },
    } as typeof snap.candidates[0] & { conflicts: unknown }
    return snap
  }

  async function openEditCheckForce(wrapper: ReturnType<typeof mount>) {
    await wrapper.get('.item-list li .link').trigger('click')
    await flushPromises()
    // 修改书名（制造未保存修改）并勾选候选与「强制关联此书」
    await wrapper.get('[data-candidate="11"] [data-field="title"]').setValue('活着（改）')
    await wrapper.get('[data-candidate="11"] input[type="checkbox"]').setValue(true)
    await wrapper.get('[data-candidate="11"] .force-link input').setValue(true)
    await wrapper.get('.actions .btn.primary').trigger('click')
    await flushPromises()
  }

  it('强制关联后修改字段：重新预览目标变化时中止确认并提示重新核对', async () => {
    let snapshotCalls = 0
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        return { ...conflictedSnapshot().candidates[0], title: '活着（改）', version: 2 }
      }
      if (String(url).endsWith('/preview-matches') && (options as RequestInit | undefined)?.method === 'POST') {
        return { matches: [] }
      }
      if (String(url).endsWith('/work-items/1/confirm')) {
        throw new Error('不应在目标变化时确认')
      }
      if (url.includes('/work-items/1')) {
        snapshotCalls += 1
        // 第 1 次初始快照冲突指向 #3；保存后的核对快照命中另一本书 #7
        return snapshotCalls === 1 ? conflictedSnapshot() : {
          ...conflictedSnapshot(),
          candidates: [{
            ...conflictedSnapshot().candidates[0],
            match_book_id: 7,
            conflicts: { code: 'isbn_ownership_conflict', message: '冲突', book_id: 7 },
          }],
        }
      }
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await openEditCheckForce(wrapper)

    const fetchMock = vi.mocked(fetch)
    // 保存 → 重新预览 → 未发确认
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/candidates/11') && (o as RequestInit | undefined)?.method === 'PATCH')).toBe(true)
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/preview-matches') && (o as RequestInit | undefined)?.method === 'POST')).toBe(true)
    expect(fetchMock.mock.calls.some(([u, o]) => String(u).endsWith('/confirm') && (o as RequestInit | undefined)?.method === 'POST')).toBe(false)
    expect(lastError.value).toContain('匹配目标已变化')
    // 中止后界面刷新为新匹配（#7），交回人工核对
    expect(wrapper.text()).toContain('#7')
  })

  it('强制关联后修改字段：目标一致时携带 force_link 确认', async () => {
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        return { ...conflictedSnapshot().candidates[0], title: '活着（改）', version: 2 }
      }
      if (String(url).endsWith('/preview-matches') && (options as RequestInit | undefined)?.method === 'POST') {
        return { matches: [] }
      }
      if (String(url).endsWith('/work-items/1/confirm')) {
        return { executions: [{ id: 9 }] }
      }
      if (url.includes('/work-items/1')) return conflictedSnapshot()
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await openEditCheckForce(wrapper)

    const confirmCall = vi.mocked(fetch).mock.calls.find(
      ([u, o]) => String(u).endsWith('/work-items/1/confirm') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(confirmCall).toBeTruthy()
    expect(JSON.parse(String((confirmCall![1] as RequestInit).body))).toEqual({
      candidate_ids: [11],
      resolutions: { 11: 'force_link' },
    })
    expect(wrapper.text()).toContain('已保存 1 条修改并确认 1 条')
  })

  it('强制关联后修改字段：冲突消解时去掉 force_link 直接确认', async () => {
    let snapshotCalls = 0
    stubFetch((url, options) => {
      if (String(url).endsWith('/candidates/11') && (options as RequestInit)?.method === 'PATCH') {
        return { ...conflictedSnapshot().candidates[0], title: '活着（改）', version: 2 }
      }
      if (String(url).endsWith('/preview-matches') && (options as RequestInit | undefined)?.method === 'POST') {
        return { matches: [] }
      }
      if (String(url).endsWith('/work-items/1/confirm')) {
        return { executions: [{ id: 9 }] }
      }
      if (url.includes('/work-items/1')) {
        snapshotCalls += 1
        if (snapshotCalls === 1) return conflictedSnapshot()
        // 修改后冲突消解：仍命中 #3 但无归属冲突
        const resolved = conflictedSnapshot()
        resolved.candidates[0] = { ...resolved.candidates[0], conflicts: null } as typeof resolved.candidates[0]
        return resolved
      }
      return { items: [{ id: 1, title: '客厅书架', status: 'in_review' }] }
    })
    const wrapper = mount(BatchIntakeView)
    await flushPromises()
    await openEditCheckForce(wrapper)

    const confirmCall = vi.mocked(fetch).mock.calls.find(
      ([u, o]) => String(u).endsWith('/work-items/1/confirm') && (o as RequestInit | undefined)?.method === 'POST',
    )
    expect(confirmCall).toBeTruthy()
    expect(JSON.parse(String((confirmCall![1] as RequestInit).body))).toEqual({ candidate_ids: [11] })
  })
})
