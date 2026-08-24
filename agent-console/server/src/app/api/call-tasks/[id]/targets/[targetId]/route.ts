/** DELETE /api/call-tasks/:id/targets/:targetId — 删除单个号码。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { remove } from '@/lib/call-targets-service';

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string; targetId: string }> },
) {
  const auth = await requirePermission('calltask:update');
  if (isDenial(auth)) return auth;
  const { targetId } = await params;
  const ok = await remove(Number(targetId), auth.tenantId);
  if (!ok) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
