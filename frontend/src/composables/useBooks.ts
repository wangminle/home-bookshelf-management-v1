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
    loading.value = true
    try {
      const data = await api.get<BookListOut>(`/books${buildQuery(40, 0)}`)
      books.value = data.items
      total.value = data.total
      hasMore.value = books.value.length < total.value
    } finally {
      loading.value = false
    }
  }

  async function loadMore() {
    if (loading.value || !hasMore.value) return
    loading.value = true
    try {
      const offset = books.value.length
      const data = await api.get<BookListOut>(`/books${buildQuery(40, offset)}`)
      books.value.push(...data.items)
      total.value = data.total
      hasMore.value = books.value.length < total.value
    } finally {
      loading.value = false
    }
  }

  // 筛选条件变化时重新加载（debounce 由调用方处理 keyword）
  watch(
    () => ({ ...filters }),
    () => loadInitial(),
    { deep: true },
  )

  return { books, total, loading, hasMore, filters, loadInitial, loadMore }
}
