/** GET /api/calls/:id — 详情聚合（session + turns + events + artifacts）。跨租户 404。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getCallDetail } from '@/lib/calls-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const detail = await getCallDetail(Number(id), auth.tenantId);
  if (!detail) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(detail);
}
