/** GET /api/calls — 列表（按 activeTenantId 隔离 + biz_type/手机号/时间筛选 + 分页）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { listCalls } from '@/lib/calls-service';

export async function GET(req: Request) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const url = new URL(req.url);
  const bizType = url.searchParams.get('bizType') || undefined;
  const phoneMasked = url.searchParams.get('phoneMasked') || undefined;
  const startFrom = url.searchParams.get('startFrom');
  const startTo = url.searchParams.get('startTo');
  const page = Number(url.searchParams.get('page') ?? '1');
  const pageSize = Number(url.searchParams.get('pageSize') ?? '20');
  const result = await listCalls({
    tenantId: auth.tenantId, bizType, phoneMasked,
    startFrom: startFrom ? new Date(startFrom) : undefined,
    startTo: startTo ? new Date(startTo) : undefined,
    page, pageSize,
  });
  return NextResponse.json(result);
}
