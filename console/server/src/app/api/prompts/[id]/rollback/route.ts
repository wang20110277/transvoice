/** POST /api/prompts/:id/rollback — 从 prompt_version 快照恢复(body { version })。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { rollback } from '@/lib/prompts-service';

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:update');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { version?: number };
  if (!body.version) return NextResponse.json({ error: 'version 必填' }, { status: 400 });
  const row = await rollback(Number(id), Number(body.version), auth.tenantId, auth.email);
  if (!row) return NextResponse.json({ error: 'not found(提示词或版本不存在)' }, { status: 404 });
  return NextResponse.json(row);
}
