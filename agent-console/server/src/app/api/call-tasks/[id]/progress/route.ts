/** GET /api/call-tasks/:id/progress — 号码状态聚合（待呼/呼叫中/已接通/失败…）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { progress } from '@/lib/call-targets-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const p = await progress(Number(id), auth.tenantId);
  if (!p) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(p);
}
