import { sessionMemberId, sessionRole } from './session'

export type LocationOperationKind = 'provision' | 'move'
export interface LocationOperation {
  key: string
  ownerId: number
  kind: LocationOperationKind
  payload: Record<string, unknown>
  label: string
  createdAt: string
  status: 'pending' | 'completed' | 'rejected'
  result?: unknown
}

function storageKey(): string {
  if (sessionRole.value !== 'owner' || !Number.isInteger(sessionMemberId.value) || !sessionMemberId.value) {
    throw new Error('请使用原 Owner 账号登录后恢复位置操作')
  }
  return `bookshelf:location-operations:v1:${import.meta.env.BASE_URL}:${sessionMemberId.value}`
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonical(record[key])}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'null'
}

export function locationOperations(): LocationOperation[] {
  const key = storageKey()
  try {
    const raw = localStorage.getItem(key)
    if (raw === null) return []
    const rows: unknown = JSON.parse(raw)
    if (!Array.isArray(rows) || !rows.every((r) => r && typeof r.key === 'string' &&
      r.ownerId === sessionMemberId.value && ['provision', 'move'].includes(r.kind) &&
      ['pending', 'completed', 'rejected'].includes(r.status) && r.payload && typeof r.payload === 'object')) {
      throw new Error('invalid journal')
    }
    return rows
  } catch {
    throw new Error('无法读取已保存的位置操作，请保留浏览器数据并核对原回执，不要重新补录')
  }
}

function save(rows: LocationOperation[]): void {
  try {
    // 未确认操作全部保留；已结束历史只保留最近50条。
    const pending = rows.filter((r) => r.status === 'pending')
    const history = rows.filter((r) => r.status !== 'pending').slice(-50)
    localStorage.setItem(storageKey(), JSON.stringify([...history, ...pending]))
  } catch {
    throw new Error('无法保存位置操作，已阻止新的写入；请启用浏览器本地存储后重试')
  }
}

export function pendingLocationOperations(): LocationOperation[] {
  return locationOperations().filter((r) => r.status === 'pending')
}

export function prepareLocationOperation(
  kind: LocationOperationKind, payload: Record<string, unknown>, label: string,
): LocationOperation {
  const rows = locationOperations()
  const signature = canonical(payload)
  const previous = rows.find((r) => r.status === 'pending' && r.kind === kind && canonical(r.payload) === signature)
  if (previous) return previous
  const op: LocationOperation = {
    key: globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    ownerId: sessionMemberId.value!, kind, payload: JSON.parse(JSON.stringify(payload)),
    label, createdAt: new Date().toISOString(), status: 'pending',
  }
  save([...rows, op])
  return op
}

export function assertOperationOwner(op: LocationOperation): void {
  storageKey()
  if (op.ownerId !== sessionMemberId.value) throw new Error('请切回原 Owner 账号恢复此操作')
  const stored = locationOperations().find((r) => r.key === op.key)
  if (!stored || canonical(stored.payload) !== canonical(op.payload) || stored.kind !== op.kind) {
    throw new Error('操作记录与已保存载荷不一致，请重新载入原操作')
  }
}

export function settleLocationOperation(op: LocationOperation, status: 'completed' | 'rejected', result?: unknown): void {
  assertOperationOwner(op)
  save(locationOperations().map((r) => r.key === op.key ? { ...r, status, result } : r))
}

/** 保留旧补录表单调用契约；真实持久记录以登录Owner隔离。 */
export function reuseProvisionIdempotencyKey(_bookId: number, items: unknown): string {
  return prepareLocationOperation('provision', { items }, '实体副本补录').key
}

export function resetProvisionIdempotencyKeys(): void {
  // 仅供原测试/显式清理已结束历史使用；绝不能删除未确认操作。
  if (sessionMemberId.value && sessionRole.value === 'owner') {
    save(locationOperations().filter((r) => r.status === 'pending'))
  }
}
