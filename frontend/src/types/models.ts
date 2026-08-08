/** 与后端 schemas/ 对齐的 TypeScript 类型定义 */

export interface ApiResponse<T = any> {
  ok: boolean
  data: T
  error: string | null
}

export interface BookOut {
  id: number
  title: string
  subtitle: string | null
  isbn13: string | null
  isbn10: string | null
  authors: string[] | null
  publisher: string | null
  publish_date: string | null
  page_count: number | null
  language: string | null
  category: string | null
  summary: string | null
  cover_path: string | null
  source: string | null
  created_at: string
  updated_at: string
}

export interface BookListOut {
  items: BookOut[]
  total: number
}

export interface BookCopy {
  id: number
  book_id: number
  copy_type: string
  format: string | null
  location: string | null
  owner_member_id: number | null
  status: string
  condition: string | null
}

export interface ReadingProgress {
  id: number
  book_id: number
  member_id: number
  status: string
  current_page: number | null
  percent: number | null
  rating: number | null
  finish_date: string | null
  updated_at: string
  message: string
}

export interface PurchaseRecord {
  id: number
  book_id: number
  price: number
  original_price: number | null
  channel: string | null
  order_no: string | null
  purchase_date: string | null
  currency: string
  buyer_member_id: number | null
  created_at: string
  message: string
}

export interface ReadingNote {
  id: number
  book_id: number
  member_id: number
  note_type: string
  content_md: string
  page: number | null
  chapter: string | null
  created_at: string
  updated_at: string
  message: string
}

export interface Attachment {
  id: number
  entity_type: string
  entity_id: number
  attach_type: string
  title: string | null
  url: string | null
  file_path: string | null
  content_md: string | null
  mime_type: string | null
  sort_order: number
  created_at: string
}

export interface CustomField {
  id: number
  field_key: string
  field_value: string | null
  value_type: string
}

export interface BookDetail extends BookOut {
  tags: string[]
  copies: BookCopy[]
  reading_progress: ReadingProgress[]
  purchase_records: PurchaseRecord[]
  reading_notes: ReadingNote[]
  attachments: Attachment[]
  custom_fields: CustomField[]
}

export interface CategoryCount {
  category: string
  count: number
}

export interface MemberStats {
  id: number
  name: string
  books_reading: number
  books_finished: number
  reading_streak: number
}

export interface StatsOut {
  total_books: number
  by_status: Record<string, number>
  by_category: CategoryCount[]
  total_spent: number
  purchase_count: number
  reading_logs_pages_total: number
  members: MemberStats[]
}

export interface MemberOut {
  id: number
  name: string
  role: string
  avatar_path: string | null
  reading_streak_offset: number
  created_at: string
  updated_at: string
}

export interface MemberListOut {
  items: MemberOut[]
  total: number
}

/** 阅读状态枚举 */
export const READING_STATUSES = [
  { value: '', label: '全部' },
  { value: 'unread', label: '想读' },
  { value: 'reading', label: '在读' },
  { value: 'finished', label: '读完' },
  { value: 'abandoned', label: '弃读' },
  { value: 'dropped', label: '放弃' },
] as const

export function statusLabel(status: string): string {
  const found = READING_STATUSES.find((s) => s.value === status)
  return found ? found.label : status
}
