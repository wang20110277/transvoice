/** GET /api/calls/:id/recording-url — 录音 presigned URL（1h）。无录音 404。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getRecordingUrl } from '@/lib/calls-service';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('call:view');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const url = await getRecordingUrl(Number(id), auth.tenantId);
  if (!url) return NextResponse.json({ error: 'no recording' }, { status: 404 });
  return NextResponse.json({ url, expiresIn: 3600 });
}
