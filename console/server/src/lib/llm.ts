/**
 * 联调 LLM — 本地 ollama(OpenAI 兼容端点)。
 *
 * 联调场景:管理员填示例变量 → renderPrompt → 以渲染后的提示词为 system,
 *   以一句开场白为 user,调 LLM 取回样例回复,用于离线验证提示词效果。
 */
import OpenAI from 'openai';

const client = new OpenAI({
  baseURL: process.env.CONSOLE_LLM_BASE_URL ?? 'http://127.0.0.1:11434/v1',
  apiKey: process.env.CONSOLE_LLM_API_KEY ?? 'ollama',
});

const MODEL = process.env.CONSOLE_LLM_MODEL ?? 'qwen3:4b-instruct';

/**
 * 以渲染后的 systemPrompt 驱动 LLM,返回开场样例回复。
 * callerUserMessage 默认一句"喂您好,请问是 X 先生/女士吗"式开场,便于评估口径。
 */
export async function chatForTest(
  systemPrompt: string,
  callerUserMessage = '（模拟接通）喂,您好。',
): Promise<string> {
  const res = await client.chat.completions.create({
    model: MODEL,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: callerUserMessage },
    ],
    max_tokens: 200,
    temperature: 0.7,
  });
  return res.choices[0]?.message?.content?.trim() ?? '';
}
