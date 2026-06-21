/** POST /api/prompts/:id/test — 联调:渲染示例变量 + 调 ollama 返回样例回复。 */
import { NextResponse } from 'next/server';
import { requirePermission, isDenial } from '@/lib/guards';
import { getById } from '@/lib/prompts-service';
import { renderPrompt } from '@/lib/prompt-template';
import { chatForTest } from '@/lib/llm';

export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const auth = await requirePermission('prompt:test');
  if (isDenial(auth)) return auth;
  const { id } = await params;
  const prompt = await getById(Number(id), auth.tenantId);
  if (!prompt) return NextResponse.json({ error: 'not found' }, { status: 404 });

  const body = (await req.json().catch(() => ({}))) as {
    variables?: Record<string, string>;
    userMessage?: string;
  };
  const rendered = renderPrompt(prompt.systemPrompt, body.variables ?? {});
  const reply = await chatForTest(rendered, body.userMessage);
  return NextResponse.json({
    rendered,
    reply,
    variables: prompt.variables,
  });
}
