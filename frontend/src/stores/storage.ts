/**
 * 实体书架位置域 API（LOC-10）。
 *
 * 契约：design/plans/实体书架与图书位置管理-功能分析和设计-20261005.md §9
 * 与 design/checkpoints/实体书架位置管理-M0契约冻结-20261006.md（LOC-02 及修订记录）：
 * - 读端点要求 locations:read（Owner/Member Web 均可），写端点仅 Owner Web + CSRF；
 * - POST 创建必传 idempotency_key；PATCH 带期望 version（幂等 key 可选）；
 * - 错误 detail 为结构化 { code, message }，用 StorageApiError.code 保留。
 */
import {
  extractApiErrorCode,
  extractApiErrorMessage,
} from '@/stores/api'
import { markSessionExpired } from '@/stores/session'
import type {
  CellCopyList,
  ShelfPhoto,
  StorageListOut,
  StorageRoom,
  StorageRoomWithStats,
  StorageShelf,
  StorageShelfDetail,
} from '@/types/models'

const BASE = `${import.meta.env.BASE_URL}api/v1`

/** 位置域错误：携带 HTTP 状态与稳定错误码（PLACEMENT_CHANGED / CODE_CONFLICT 等） */
export class StorageApiError extends Error {
  status: number
  code: string | null

  constructor(message: string, status: number, code: string | null) {
    super(message)
    this.name = 'StorageApiError'
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', 'X-UI-Client': 'web', ...(options?.headers || {}) },
    ...options,
  })
  if (res.status === 401) {
    markSessionExpired()
  }
  let raw: unknown = {}
  try {
    raw = await res.json()
  } catch {
    // HTTP 成功但回执截断：不能当成业务成功（BUG-299）。失败响应仍走原错误文案。
    if (res.ok) {
      throw new StorageApiError('响应无法解析，结果未确认', res.status, null)
    }
  }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new StorageApiError('响应格式无效，结果未确认', res.status, null)
  }
  const body = raw as Record<string, unknown>
  if (!res.ok || body.ok === false) {
    throw new StorageApiError(
      extractApiErrorMessage(body, res.status, '请求失败'),
      res.status,
      extractApiErrorCode(body),
    )
  }
  if (body.data == null) {
    throw new StorageApiError('响应缺少回执，结果未确认', res.status, null)
  }
  return body.data as T
}

/** 生成幂等键：优先 crypto.randomUUID，旧环境退化为时间戳+随机串 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto
  if (c && typeof c.randomUUID === 'function') {
    return c.randomUUID()
  }
  return `web-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
}

export { reuseProvisionIdempotencyKey, resetProvisionIdempotencyKeys,
  prepareLocationOperation, pendingLocationOperations } from './storageOperations'
import { assertOperationOwner, settleLocationOperation } from './storageOperations'
import type { LocationOperation } from './storageOperations'
export type { LocationOperation } from './storageOperations'

// ── 房间 ──

export interface RoomCreatePayload {
  idempotency_key: string
  code: string
  name: string
  description?: string | null
  sort_order?: number
}

export interface RoomUpdatePayload {
  version: number
  code?: string
  name?: string
  description?: string | null
  sort_order?: number
  archived?: boolean
  idempotency_key?: string
}

export function listRooms(includeArchived: boolean): Promise<StorageListOut<StorageRoomWithStats>> {
  return request(`/storage/rooms?include_archived=${includeArchived}&limit=100&offset=0`)
}

export function createRoom(payload: RoomCreatePayload): Promise<StorageRoom> {
  return request('/storage/rooms', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateRoom(roomId: number, payload: RoomUpdatePayload): Promise<StorageRoom> {
  return request(`/storage/rooms/${roomId}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

// ── 书架 ──

export interface ShelfLayerInput {
  label?: string
  cells?: { label?: string }[]
}

export interface ShelfCreatePayload {
  idempotency_key: string
  room_id: number
  code: string
  name: string
  position_note?: string | null
  sort_order?: number
  layers?: ShelfLayerInput[]
}

export interface ShelfUpdatePayload {
  version: number
  code?: string
  name?: string
  position_note?: string | null
  sort_order?: number
  archived?: boolean
  idempotency_key?: string
}

export function listShelves(roomId: number, includeArchived: boolean): Promise<StorageListOut<StorageShelf>> {
  return request(`/storage/shelves?room_id=${roomId}&include_archived=${includeArchived}&limit=100&offset=0`)
}

export function getShelf(shelfId: number): Promise<StorageShelfDetail> {
  return request(`/storage/shelves/${shelfId}`)
}

export function createShelf(payload: ShelfCreatePayload): Promise<StorageShelfDetail> {
  return request('/storage/shelves', { method: 'POST', body: JSON.stringify(payload) })
}

export function updateShelf(shelfId: number, payload: ShelfUpdatePayload): Promise<StorageShelfDetail> {
  return request(`/storage/shelves/${shelfId}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

/** 书架照片内容 URL（受认证读取，locations:read + files:read 双 Scope） */
export function shelfPhotoUrl(photoId: number): string {
  return `${BASE}/storage/photos/${photoId}/content`
}

// ── 布局（PUT 全量表达，LOC-11；契约修订记录 LOC-06） ──
// layers 是书架层结构的全量表达：未列出的现存活动层/格视为归档请求
// （有副本引用则 409 LOCATION_IN_USE）；既有层/格带回 id 与 version 保稳定 ID，
// 新增层/格不带 id；cell code 由服务端自动编号（架内唯一不复用）。

export interface LayoutCellInput {
  id?: number
  version?: number
  code?: string
  label?: string
  sort_order?: number
  archived?: boolean
}

export interface LayoutLayerInput {
  id?: number
  version?: number
  label?: string
  sort_order?: number
  archived?: boolean
  cells?: LayoutCellInput[]
}

export interface LayoutPutPayload {
  idempotency_key: string
  shelf_version: number
  layers: LayoutLayerInput[]
}

export function putShelfLayout(shelfId: number, payload: LayoutPutPayload): Promise<StorageShelfDetail> {
  return request(`/storage/shelves/${shelfId}/layout`, { method: 'PUT', body: JSON.stringify(payload) })
}

// ── 格子副本清单（LOC-08/LOC-12） ──

export function listCellCopies(cellId: number, limit = 20, offset = 0): Promise<CellCopyList> {
  return request(`/storage/cells/${cellId}/copies?limit=${limit}&offset=${offset}`)
}

// ── 书架照片（LOC-09/LOC-12） ──

export interface PhotoUpdatePayload {
  version: number
  caption?: string | null
  is_primary?: boolean
  idempotency_key?: string
}

/** 上传书架照片（multipart；浏览器自建 boundary，不能带 JSON Content-Type） */
export async function uploadShelfPhoto(
  shelfId: number,
  file: File,
  options: { caption?: string; is_primary?: boolean; idempotency_key: string },
): Promise<ShelfPhoto> {
  const form = new FormData()
  form.append('image', file)
  if (options.caption) form.append('caption', options.caption)
  if (options.is_primary) form.append('is_primary', 'true')
  form.append('idempotency_key', options.idempotency_key)
  const res = await fetch(`${BASE}/storage/shelves/${shelfId}/photos`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-UI-Client': 'web' },
    body: form,
  })
  if (res.status === 401) {
    markSessionExpired()
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok || body.ok === false) {
    throw new StorageApiError(
      extractApiErrorMessage(body, res.status, '上传失败'),
      res.status,
      extractApiErrorCode(body),
    )
  }
  return body.data as ShelfPhoto
}

export function updateShelfPhoto(photoId: number, payload: PhotoUpdatePayload): Promise<ShelfPhoto> {
  return request(`/storage/photos/${photoId}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

/** 删除照片；期望版本走 query（DELETE 不带请求体，契约修订记录 LOC-09） */
export function deleteShelfPhoto(photoId: number, version: number, idempotencyKey?: string): Promise<unknown> {
  const qs = new URLSearchParams({ version: String(version) })
  if (idempotencyKey) qs.set('idempotency_key', idempotencyKey)
  return request(`/storage/photos/${photoId}?${qs.toString()}`, { method: 'DELETE' })
}

// ── 单册位置与副本补录（LOC-13/14，LOC-15 前端对接） ──

export interface PlacementTargetInput {
  shelf_id: number
  cell_id?: number | null
}

/**
 * 单册分配/移动/清除（PATCH copies placement，仅 Owner Web）。
 * target 非 null=分配/移动（不得带 keep_legacy_location）；
 * target=null=清除，必须显式 keep_legacy_location（契约修订 LOC-13）。
 */
export interface PlacementUpdatePayload {
  placement_version: number
  target?: PlacementTargetInput | null
  keep_legacy_location?: boolean
  idempotency_key?: string
}

export function setCopyPlacement(
  bookId: number,
  copyId: number,
  payload: PlacementUpdatePayload,
): Promise<unknown> {
  return request(`/books/${bookId}/copies/${copyId}/placement`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export interface IntakeNewCopiesInput {
  count: number
  owner_member_id: number
  target: PlacementTargetInput
}

/** 存量副本补录提交（POST /storage/copies）：数量只来自显式确认（LOC-01 冻结） */
export interface IntakeCommitPayload {
  idempotency_key: string
  items: {
    book_id: number
    assign?: { copy_id: number; placement_version: number; target: PlacementTargetInput }[]
    new_copies?: IntakeNewCopiesInput
  }[]
}

export interface IntakeCommitResult {
  items: { book_id: number; book_title: string; assigned: unknown[]; created: number[] }[]
  assigned: number
  created: number
}

export function provisionCopies(payload: IntakeCommitPayload): Promise<IntakeCommitResult> {
  return request('/storage/copies', { method: 'POST', body: JSON.stringify(payload) })
}

// ── 未定位清单与批量移动（LOC-16） ──
export interface UnlocatedCopy {
  copy_id: number; book_id: number; book_title: string; owner_member_id: number | null
  owner_member_name: string | null; status: string; location: string | null
}
export interface UnlocatedBook { book_id: number; book_title: string; digital_copies: number }
export interface UnlocatedPage {
  copies_without_placement: { items: UnlocatedCopy[]; total: number }
  legacy_location_only: { items: UnlocatedCopy[]; total: number }
  books_without_physical_copy: { items: UnlocatedBook[]; total: number }
}
export function listUnlocated(limit = 20, offset = 0): Promise<UnlocatedPage> {
  return request(`/storage/unlocated?limit=${limit}&offset=${offset}`)
}
export interface MovePreview {
  target: PlacementTargetInput & { shelf_version: number; cell_version: number | null; display: string }
  copies: { copy_id: number; book_id: number; book_title: string; placement_version: number;
    from: { display: string | null }; to: { display: string | null } }[]
  preview_digest: string
}
export function previewPlacement(target: PlacementTargetInput, copyIds: number[]): Promise<MovePreview> {
  return request('/storage/placements/preview', { method: 'POST',
    body: JSON.stringify({ target, copies: copyIds.map((copy_id) => ({ copy_id })) }) })
}

function validResult(op: LocationOperation, value: unknown): boolean {
  if (!value || typeof value !== 'object') return false
  const r = value as Record<string, any>
  const positiveIds = (ids: unknown) => Array.isArray(ids) && ids.every((id) => Number.isInteger(id) && id > 0)
  if (op.kind === 'provision') {
    const items = op.payload.items as IntakeCommitPayload['items']
    if (!Array.isArray(items) || !Array.isArray(r.items) || r.items.length !== items.length) return false
    if (r.created !== items.reduce((sum, i) => sum + (i.new_copies?.count ?? 0), 0) ||
        r.assigned !== items.reduce((sum, i) => sum + (i.assign?.length ?? 0), 0)) return false
    return items.every((i) => {
      const rows = r.items.filter((row: any) => row?.book_id === i.book_id)
      if (rows.length !== 1) return false
      const row = rows[0]
      return positiveIds(row.created) && row.created.length === (i.new_copies?.count ?? 0) &&
        Array.isArray(row.assigned) && row.assigned.length === (i.assign?.length ?? 0)
    })
  }
  const copies = op.payload.copies as { copy_id: number; placement_version: number }[]
  return Array.isArray(copies) && r.moved === copies.length && Array.isArray(r.copies) &&
    r.copies.length === copies.length && copies.every((c) => r.copies.some((row: any) =>
      row.copy_id === c.copy_id && row.placement_version === c.placement_version + 1))
}

export async function checkLocationOperation(op: LocationOperation): Promise<unknown | null> {
  assertOperationOwner(op)
  const receipt = await request<{ status: string; idempotency_key: string; result?: unknown }>(
    `/storage/operations/${encodeURIComponent(op.key)}`)
  if (receipt.idempotency_key !== op.key) throw new Error('回执标识不一致，结果未确认')
  if (receipt.status !== 'completed') return null // not_found 不代表未执行，原key继续保留
  if (!validResult(op, receipt.result)) throw new Error('回执内容不完整，结果未确认')
  settleLocationOperation(op, 'completed', receipt.result)
  return receipt.result
}

export async function sendLocationOperation(op: LocationOperation): Promise<unknown> {
  assertOperationOwner(op)
  try {
    const result = await request(op.kind === 'provision' ? '/storage/copies' : '/storage/placements', {
      method: 'POST', body: JSON.stringify({ ...op.payload, idempotency_key: op.key }),
    })
    if (!validResult(op, result)) throw new Error('回执内容不完整，结果未确认，请查询回执或重试原操作')
    settleLocationOperation(op, 'completed', result)
    return result
  } catch (e) {
    // 确定的业务拒绝可重新预览；鉴权/限流/网络/5xx仍保留原操作。
    if (e instanceof StorageApiError && [400, 404, 409, 413, 422].includes(e.status)) {
      settleLocationOperation(op, 'rejected')
    }
    throw e
  }
}

export function getStorageBook(bookId: number): Promise<import('@/types/models').BookDetail> {
  return request(`/books/${bookId}`)
}
