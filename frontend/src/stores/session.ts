import { ref } from 'vue'

/**
 * 会话状态（权限阶段 1，CHK-071 修复）。
 * 后端会话端点挂载在根路径 /auth/session（web_auth 路由不在 /api/v1 下）；
 * SPA 内登录/登出后必须失效缓存，否则路由守卫会拿到过期结论。
 */
export const sessionAuthenticated = ref<boolean | null>(null)
/** 权限阶段 2：会话角色与成员 ID（前端只做展示分流，权限判定仍在后端） */
export const sessionRole = ref<string | null>(null)
export const sessionMemberId = ref<number | null>(null)
export const sessionMemberName = ref<string | null>(null)

/** 探测会话；force=true 绕过缓存（登录/登出后调用）。 */
export async function probeSession(force = false): Promise<boolean> {
  if (!force && sessionAuthenticated.value !== null) {
    return sessionAuthenticated.value
  }
  try {
    const res = await fetch(`${import.meta.env.BASE_URL}auth/session`, {
      credentials: 'include',
    })
    const body = await res.json()
    sessionAuthenticated.value = Boolean(body?.authenticated)
    sessionRole.value = body?.role ?? null
    sessionMemberId.value = body?.member_id ?? null
    sessionMemberName.value = body?.member_name ?? null
  } catch {
    sessionAuthenticated.value = false
    sessionRole.value = null
    sessionMemberId.value = null
    sessionMemberName.value = null
  }
  return sessionAuthenticated.value
}

/** 登录/登出后失效缓存，下一次守卫触发时重新探测。 */
export function invalidateSession(): void {
  sessionAuthenticated.value = null
  sessionRole.value = null
  sessionMemberId.value = null
  sessionMemberName.value = null
}
