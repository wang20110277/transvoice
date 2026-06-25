/** POST /api/calls/:id/archive-recording — 手动归档录音（转发 agent-flow）。
 *
 * 自动归档失败时的兜底：console 校验租户归属后转发到 agent-flow，透传其状态码与 body
 * （200 成功 / 409 已归档 / 410 文件已清理 / 502 MinIO 不可用）。跨租户或不存在的通话 → 404，
 * 不泄漏存在性。
 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { archiveRecording } from '@/lib/calls-service';

export async function POST(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const result = await archiveRecording(Number(id), auth.tenantId);
  return NextResponse.json(result.body, { status: result.status });
}
