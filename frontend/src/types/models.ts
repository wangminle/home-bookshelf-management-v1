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

/** 副本结构化位置（LOC-05/LOC-08）：仅 locations:read 主体的响应中出现 */
export interface CopyPlacement {
  shelf_id: number | null
  cell_id: number | null
  /** placement_version：位置变更的期望版本 */
  version: number
}

export interface BookCopy {
  id: number
  book_id: number
  copy_type: string
  format: string | null
  location: string | null
  file_path: string | null
  owner_member_id: number | null
  acquire_type: string | null
  status: string
  condition: string | null
  created_at: string
  updated_at: string
  /**
   * 输出隔离（LOC-05）：未授权主体的响应中 placement/placement_version/location_display 键完全不出现；
   * 前端必须按「键缺失」健壮处理，不能当作 null。
   * placement 为 null 表示已授权但该副本无结构化定位。
   */
  placement?: CopyPlacement | null
  /** 真实位置版本，未定位或清除后也返回（BUG-298）；未授权时键缺失 */
  placement_version?: number
  /** 实时位置路径；无结构化定位时回退旧 location 文字 */
  location_display?: string | null
}

export interface ReadingProgress {
  id: number
  book_id: number
  member_id: number
  status: string
  current_page: number | null
  percent: number | null
  rating: number | null
  to_read: boolean
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
  catalog_visibility?: string | null
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
  by_year: YearlyStat[]
}

export interface YearlyStat {
  year: string
  books_added: number
  spent: number
  pages_read: number
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

// ── 实体书架与位置（LOC-10；契约：design/plans §6/§9 + M0 契约冻结 LOC-02） ──
// 位置域错误 detail 为 {code, message}；统计口径 §6.3 四值。

/** 位置数量统计（§6.3）：已分配/在位/在架/涉及书目数，口径互不混算 */
export interface LocationStats {
  assigned_copies: number
  present_copies: number
  on_shelf_copies: number
  books_involved: number
}

export interface StorageRoom {
  id: number
  code: string
  name: string
  description: string | null
  sort_order: number
  version: number
  archived_at: string | null
  created_at: string
  updated_at: string
}

/** GET /storage/rooms 清单项：附加房间聚合统计 */
export interface StorageRoomWithStats extends StorageRoom {
  stats: LocationStats
}

export interface StorageShelf {
  id: number
  room_id: number
  code: string
  name: string
  position_note: string | null
  sort_order: number
  version: number
  archived_at: string | null
  created_at: string
  updated_at: string
}

export interface ShelfCell {
  id: number
  shelf_id: number
  layer_id: number
  code: string
  label: string
  sort_order: number
  version: number
  archived_at: string | null
  created_at: string
  updated_at: string
}

export interface ShelfLayer {
  id: number
  shelf_id: number
  label: string
  sort_order: number
  version: number
  archived_at: string | null
  created_at: string
  updated_at: string
  cells: ShelfCell[]
}

export interface ShelfPhoto {
  id: number
  shelf_id: number
  caption: string | null
  is_primary: boolean
  width: number
  height: number
  mime_type: string
  version: number
  created_at: string
  updated_at: string
}

/** GET /storage/shelves/{id} 详情：结构 + 统计 + 受授权照片引用 */
export interface StorageShelfDetail extends StorageShelf {
  layers: ShelfLayer[]
  stats: LocationStats
  photos: ShelfPhoto[]
}

/** GET /storage/cells/{id}/copies 格子副本清单项（LOC-08/LOC-12） */
export interface CellCopyItem {
  copy_id: number
  book_id: number
  book_title: string
  owner_member_id: number | null
  owner_member_name: string | null
  status: string
  format: string | null
  condition: string | null
  location_display: string | null
}

export interface CellCopyList {
  cell_id: number
  shelf_id: number
  location_display: string | null
  items: CellCopyItem[]
  total: number
}

/** 位置域清单响应（items + total 分页） */
export interface StorageListOut<T> {
  items: T[]
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

/** 副本状态（后端 CopyStatus 枚举）中文标签 */
export const COPY_STATUS_LABELS: Record<string, string> = {
  in_shelf: '在架',
  storage: '收纳',
  lent_out: '外借',
  lost: '遗失',
  damaged: '损坏',
  discarded: '已处理',
}

export function copyStatusLabel(status: string): string {
  return COPY_STATUS_LABELS[status] ?? status
}

/** §6.3：在位 = in_shelf / storage；外借/遗失/损坏/已处理不计在位 */
export function isInPlaceStatus(status: string): boolean {
  return status === 'in_shelf' || status === 'storage'
}
