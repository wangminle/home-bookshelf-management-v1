import { ref, reactive, watch } from 'vue'
import type { BookOut, BookListOut } from '@/types/models'
import { useApiStore } from '@/stores/api'

export interface BookFilters {
  keyword: string
  status: string
  category: string
}

export function useBooks() {
  const api = useApiStore()
  const books = ref<BookOut[]>([])
  const total = ref(0)
  const loading = ref(false)
  const hasMore = ref(true)
  // 修复 BUG：请求序号，防止快速切换筛选时旧响应覆盖新结果
  let requestId = 0

  const filters = reactive<BookFilters>({
    keyword: '',
    status: '',
    category: '',
  })

  function buildQuery(limit: number, offset: number): string {
    const params = new URLSearchParams()
    params.set('limit', String(limit))
    params.set('offset', String(offset))
    if (filters.keyword) params.set('keyword', filters.keyword)
    if (filters.status) {
      params.set('status', filters.status)
      // status 过滤需配合 member_id（后端规则：member_id 无 status→400，status 可单独用）
      // 不带 member_id 则按全局 status 过滤
    }
    if (filters.category) params.set('category', filters.category)
    return `?${params.toString()}`
  }

  async function loadInitial() {
    const currentId = ++requestId
    loading.value = true
    try {
      const data = await api.get<BookListOut>(`/books${buildQuery(40, 0)}`)
      if (currentId !== requestId) return // 丢弃过期响应
      books.value = data.items
      total.value = data.total
      hasMore.value = books.value.length < total.value
    } finally {
      if (currentId === requestId) loading.value = false
    }
  }

  async function loadMore() {
    if (loading.value || !hasMore.value) return
    // 分页沿用当前 requestId，不自增——避免误伤并发的 loadInitial；
    // 筛选触发的 loadInitial 会 ++requestId，从而自动丢弃过期的 loadMore
    const currentId = requestId
    loading.value = true
    try {
      const offset = books.value.length
      const data = await api.get<BookListOut>(`/books${buildQuery(40, offset)}`)
      if (currentId !== requestId) return // 丢弃过期响应
      books.value.push(...data.items)
      total.value = data.total
      hasMore.value = books.value.length < total.value
    } finally {
      if (currentId === requestId) loading.value = false
    }
  }

  // 筛选条件变化时重新加载（debounce 由调用方处理 keyword）
  // 使用 getter 返回各字段值数组，仅在实际值变化时触发
  watch(
    () => [filters.keyword, filters.status, filters.category],
    () => {
      // 筛选变化后回到顶部（修复 4.5：用户在滚动较远位置时列表重置但页面不回顶）
      window.scrollTo({ top: 0 })
      // 立刻作废进行中的分页请求，再拉第一页
      requestId += 1
      loadInitial()
    },
  )

  return { books, total, loading, hasMore, filters, loadInitial, loadMore }
}
