/** GET / POST /api/call-tasks/:id/targets — 号码清单列表 + 录入（单条/CSV 批量）。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { listByTask, create, bulkCreateFromCsv } from '@/lib/call-targets-service';

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
    csv?: string;            // CSV 批量（每行一个号码）
    maxAttempts?: number;    // 可选，默认 1
  };
  const maxAttempts = body.maxAttempts ?? 1;

  if (body.csv !== undefined) {
    const result = await bulkCreateFromCsv(
      Number(id), auth.tenantId, body.csv, maxAttempts, auth.email,
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
  return NextResponse.json({ error: '需要 phone 或 csv 字段' }, { status: 400 });
}
