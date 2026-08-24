/** POST /api/prompts/:id/publish — 翻 is_active=true + 清 Redis 缓存(零延迟生效)。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { publish } from '@/lib/prompts-service';

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:update');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const row = await publish(Number(id), auth.tenantId, auth.email);
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(row);
}
