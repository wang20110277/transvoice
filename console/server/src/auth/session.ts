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

/** 取当前登录用户的 tenantId;未登录抛错(调用方应先过认证守卫)。 */
export async function requireTenantId(): Promise<string> {
  const session = await getSession();
  if (!session) throw new Error('UNAUTHORIZED');
  return (session.user as { tenantId?: string }).tenantId ?? 'default';
}

/** 取当前登录用户 email,用作 create_user/update_user 审计列。 */
export async function requireUserEmail(): Promise<{ email: string; tenantId: string }> {
  const session = await getSession();
  if (!session) throw new Error('UNAUTHORIZED');
  return {
    email: session.user.email,
    tenantId: (session.user as { tenantId?: string }).tenantId ?? 'default',
  };
}
