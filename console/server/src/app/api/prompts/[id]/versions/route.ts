/** GET /api/prompts/:id/versions — 版本历史(prompt_version 快照,按版本倒序)。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getVersions } from '@/lib/prompts-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const rows = await getVersions(Number(id), auth.tenantId);
  if (!rows) return NextResponse.json({ error: 'not found' }, { status: 404 });
  return NextResponse.json({ versions: rows });
}
