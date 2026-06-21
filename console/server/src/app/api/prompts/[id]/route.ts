/** GET /api/prompts/:id 详情;PUT 编辑(version++ + 快照);DELETE 删除。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getById, update, remove, type PromptInput } from '@/lib/prompts-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const row = await getById(Number(id), auth.tenantId);
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(row);
}

export async function PUT(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:update');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json()) as Partial<PromptInput> & { systemPrompt: string };
  if (!body.systemPrompt) {
    return NextResponse.json({ error: 'systemPrompt 必填' }, { status: 400 });
  }
  const row = await update(Number(id), body, auth.tenantId, auth.email);
  if (!row) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json(row);
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:delete');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const ok = await remove(Number(id), auth.tenantId);
  if (!ok) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
