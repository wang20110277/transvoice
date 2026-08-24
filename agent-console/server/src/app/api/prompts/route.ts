/** GET /api/prompts — 列表(按 session.tenantId 过滤);POST — 新建草稿。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial, conflict, isUniqueViolation } from '@/lib/guards';
import { listByTenant, create, type PromptInput } from '@/lib/prompts-service';

export async function GET() {
  const auth = await requirePermission('prompt:view');
  if (isDenial(auth)) return auth;
  const rows = await listByTenant(auth.tenantId);
  return NextResponse.json({ prompts: rows });
}

export async function POST(req: Request) {
  const auth = await requirePermission('prompt:create');
  if (isDenial(auth)) return auth;
  const body = (await req.json()) as Partial<PromptInput>;
  if (!body.title || !body.bizType || !body.scenario || !body.systemPrompt) {
    return NextResponse.json({ error: 'title/bizType/scenario/systemPrompt 必填' }, { status: 400 });
  }
  try {
    const row = await create(body as PromptInput, auth.tenantId, auth.email);
    return NextResponse.json(row, { status: 201 });
  } catch (e) {
    if (isUniqueViolation(e)) return conflict('该 (biz_type, scenario) 已存在,请换一个 scenario');
    throw e;
  }
}
