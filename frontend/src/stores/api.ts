import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { ApiResponse } from '@/types/models'

const BASE = '/api/v1'

/** 全局错误提示（简易实现，无 UI 库依赖） */
export const lastError = ref<string | null>(null)

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  const body: ApiResponse<T> = await res.json().catch(() => ({
    ok: false,
    data: null as any,
    error: `HTTP ${res.status}`,
  }))
  if (!body.ok) {
    lastError.value = body.error || `请求失败 (${res.status})`
    throw new Error(body.error || `请求失败 (${res.status})`)
  }
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
