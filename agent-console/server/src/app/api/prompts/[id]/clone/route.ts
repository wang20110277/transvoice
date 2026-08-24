/** POST /api/prompts/:id/clone — 克隆到新 scenario(body 可选 { scenario })。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial, conflict, isUniqueViolation } from '@/lib/guards';
import { clone } from '@/lib/prompts-service';

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:create');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json().catch(() => ({}))) as { scenario?: string };
  try {
    const row = await clone(Number(id), auth.tenantId, auth.email, body.scenario);
    if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
    return NextResponse.json(row, { status: 201 });
  } catch (e) {
    if (isUniqueViolation(e)) return conflict('目标 scenario 已存在,请指定其他 scenario');
    throw e;
  }
}
