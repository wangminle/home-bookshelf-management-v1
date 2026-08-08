import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ApiResponse } from '@/types/models'

const BASE = '/api/v1'

/** 全局错误提示（简易实现，无 UI 库依赖） */
export const lastError = ref<string | null>(null)

/** 后端连接状态：首次 load 成功后置 true，失败置 false */
export const backendOnline = ref<boolean | null>(null)

/** 后端是否处于离线状态（用于显示连接提示） */
export const backendOffline = ref(false)

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
      ...options,
    })
  } catch (e) {
    // 修复 P2：捕获 fetch 本身的网络错误（离线、DNS 失败、连接拒绝）
    backendOnline.value = false
    backendOffline.value = true
    const msg = e instanceof TypeError ? '无法连接到服务器，请检查后端是否启动' : '网络请求失败'
    lastError.value = msg
    throw new Error(msg)
  }
  const body: ApiResponse<T> = await res.json().catch(() => ({
    ok: false,
    data: null as any,
    error: `HTTP ${res.status}`,
  }))
  if (!body.ok) {
    // BUG-096 修复：兼容 FastAPI 错误格式 { detail: "..." } 和验证错误 { detail: [{ msg }] }
    const raw = body as any
    let msg = body.error || raw.detail
    if (Array.isArray(msg)) {
      msg = msg.map((e: any) => e.msg || JSON.stringify(e)).join('; ')
    }
    msg = msg || `请求失败 (${res.status})`
    lastError.value = msg
    throw new Error(msg)
  }
  // 请求成功，后端在线
  backendOnline.value = true
  backendOffline.value = false
  lastError.value = null
  return body.data
}

export const useApiStore = defineStore('api', () => {
  async function get<T>(path: string): Promise<T> {
    return request<T>(path)
  }

  async function post<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: 'POST',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  async function patch<T>(path: string, body?: unknown): Promise<T> {
    return request<T>(path, {
      method: 'PATCH',
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  }

  return { get, post, patch }
})

/** 构造封面图片 URL */
export function coverUrl(coverPath: string | null): string | null {
  if (!coverPath) return null
  // cover_path 形如 "covers/abc.jpg" → /api/v1/files/covers/abc.jpg
  const parts = coverPath.split('/')
  const dir = parts[0] // "covers" 或 "attachments"
  const file = parts.slice(1).join('/')
  return `${BASE}/files/${dir}/${file}`
}

/** 构造附件文件 URL */
export function attachmentUrl(filePath: string | null): string | null {
  if (!filePath) return null
  const parts = filePath.split('/')
  const dir = parts[0]
  const file = parts.slice(1).join('/')
  return `${BASE}/files/${dir}/${file}`
}

/** 安全 URL：仅允许 http/https/mailto 协议，阻止 javascript: / data: 等危险 scheme (BUG-095) */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null
  const trimmed = url.trim()
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:' || parsed.protocol === 'mailto:') {
      return parsed.href
    }
    return null
  } catch {
    // 非绝对 URL；允许站内相对路径（以 / 开头但非 //）
    if (trimmed.startsWith('/') && !trimmed.startsWith('//')) {
      return trimmed
    }
    return null
  }
}
