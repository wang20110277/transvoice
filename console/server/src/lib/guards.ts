/** Route Handler 认证 + RBAC 守卫。返回 AuthCtx 或 NextResponse(错误响应)。 */
import { NextResponse } from 'next/server';
import { getSession } from '@/auth/session';
import { hasPermission, type PermissionCode } from './permissions';

export interface AuthCtx {
  email: string;
  tenantId: string;
  role: string;
}

function ctxFromSession(session: NonNullable<Awaited<ReturnType<typeof getSession>>>): AuthCtx {
  const u = session.user as { email: string; tenantId?: string; role?: string };
  const s = session as {
    activeTenantId?: string | null;
    session?: { activeTenantId?: string | null };
  };
  const active = s.activeTenantId ?? s.session?.activeTenantId ?? null;
  // 活跃租户优先,空时 fallback user.tenantId
  return { email: u.email, tenantId: active ?? u.tenantId ?? 'default', role: u.role ?? 'admin' };
}

/** 要求已登录;未登录 401。 */
export async function requireAuth(): Promise<AuthCtx | NextResponse> {
  const session = await getSession();
  if (!session) return NextResponse.json({ error: 'unauthorized' }, { status: 401 });
  return ctxFromSession(session);
}

/** 要求登录 + 具备指定权限;不满足分别 401 / 403。 */
export async function requirePermission(
  code: PermissionCode,
): Promise<AuthCtx | NextResponse> {
  const auth = await requireAuth();
  if (auth instanceof NextResponse) return auth;
  if (!hasPermission(auth.role, code)) {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  }
  return auth;
}

/** 要求登录 + 为 platform_admin(跨租户管理);否则 401 / 403。 */
export async function requirePlatformAdmin(): Promise<AuthCtx | NextResponse> {
  const auth = await requireAuth();
  if (auth instanceof NextResponse) return auth;
  if (auth.role !== 'platform_admin') {
    return NextResponse.json({ error: 'forbidden: platform_admin required' }, { status: 403 });
  }
  return auth;
}

/** 判定结果是否为守卫返回的错误响应。 */
export function isDenial(x: AuthCtx | NextResponse): x is NextResponse {
  return x instanceof NextResponse;
}

/** 唯一约束冲突 → 409(biz_type+scenario 已存在)。 */
export function conflict(msg: string) {
  return NextResponse.json({ error: msg }, { status: 409 });
}

/**
 * 判定 pg 唯一约束冲突(23505)。Drizzle(node-postgres) 把原始错误包在 cause 里,
 * 外层 Error 的 .code 为空,真实 code 在 .cause.code,故两层都查。
 */
export function isUniqueViolation(e: unknown): boolean {
  const code = (e: unknown): string | undefined => {
    if (typeof e !== 'object' || e === null) return undefined;
    const c = (e as { code?: unknown }).code;
    return typeof c === 'string' ? c : undefined;
  };
  return code(e) === '23505' || code((e as { cause?: unknown })?.cause) === '23505';
}
