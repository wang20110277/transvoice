/** GET / POST /api/call-tasks/:id/targets — 号码清单列表 + 录入（单条/结构化批量）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { listByTask, create, bulkCreateStructured } from '@/lib/call-targets-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const targets = await listByTask(Number(id), auth.tenantId);
  return NextResponse.json({ targets });
}

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('calltask:update');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const body = (await req.json()) as {
    phone?: string;          // 单条录入
    targets?: { phone: string; customerId?: string; vars?: Record<string, string> }[]; // 结构化批量
    maxAttempts?: number;    // 可选，默认 1
  };
  const maxAttempts = body.maxAttempts ?? 1;

  if (Array.isArray(body.targets)) {
    if (body.targets.length === 0) {
      return NextResponse.json({ error: 'targets 不能为空' }, { status: 400 });
    }
    const result = await bulkCreateStructured(
      Number(id), auth.tenantId, body.targets, maxAttempts, auth.email,
    );
    return NextResponse.json(result);
  }
  if (body.phone) {
    const row = await create(Number(id), auth.tenantId, body.phone, maxAttempts, auth.email);
    if (!row) {
      return NextResponse.json({ error: '号码已存在或任务不存在' }, { status: 409 });
    }
    return NextResponse.json(row);
  }
  return NextResponse.json({ error: '需要 phone 或 targets 字段' }, { status: 400 });
}
