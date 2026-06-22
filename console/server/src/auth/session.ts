/**
 * Server-side session 获取。Route Handler / Server Component 内调用。
 * 返回 { user: { id, email, name, tenantId, role } } 或 null。
 */
import { headers } from 'next/headers';
import { auth } from '@/auth';

export async function getSession() {
  const session = await auth.api.getSession({ headers: await headers() });
  return session;
}

/**
 * 活跃租户优先级:session.activeTenantId(切换后) > user.tenantId(主) > 'default'。
 * activeTenantId 由 better-auth session additionalFields 注入,可能挂在顶层或 session 子对象,
 * 两层都查以容错不同 better-auth 版本。
 */
export function activeTenantIdOf(session: unknown): string | null {
  const s = session as {
    activeTenantId?: string | null;
    session?: { activeTenantId?: string | null };
  };
  return s.activeTenantId ?? s.session?.activeTenantId ?? null;
}

/** 取当前活跃租户;未登录抛错(调用方应先过认证守卫)。 */
export async function requireTenantId(): Promise<string> {
  const session = await getSession();
  if (!session) throw new Error('UNAUTHORIZED');
  const tenantId = (session.user as { tenantId?: string }).tenantId ?? 'default';
  return activeTenantIdOf(session) ?? tenantId;
}

/** 显式别名,语义=活跃租户(与 requireTenantId 同逻辑)。 */
export async function requireActiveTenantId(): Promise<string> {
  return requireTenantId();
}

/** 取当前登录用户 email + 活跃租户,用作审计列与隔离键。 */
export async function requireUserEmail(): Promise<{ email: string; tenantId: string }> {
  const session = await getSession();
  if (!session) throw new Error('UNAUTHORIZED');
  const tenantId = (session.user as { tenantId?: string }).tenantId ?? 'default';
  return {
    email: session.user.email,
    tenantId: activeTenantIdOf(session) ?? tenantId,
  };
}
