/** 前端提示词 API 客户端(浏览器 fetch)。 */
export interface PromptDTO {
  id: number;
  tenantId: string;
  bizType: string;
  scenario: string;
  systemPrompt: string;
  maxReplyLength: number;
  isActive: boolean;
  version: number;
  description: string | null;
  title: string;
  category: string;
  variables: string[];
  createUser: string;
  updateUser: string;
  updateTime: string;
}

export interface VersionDTO {
  id: number;
  version: number;
  systemPrompt: string;
  snapshot: { title?: string; category?: string; variables?: string[] };
  updateUser: string;
  updateTime: string;
}

export interface PromptInput {
  title: string;
  bizType: string;
  scenario: string;
  systemPrompt: string;
  category?: string;
  maxReplyLength?: number;
  description?: string | null;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { error?: string }).error ?? `HTTP ${res.status}`);
  return data as T;
}

export const promptsApi = {
  list: () => req<{ prompts: PromptDTO[] }>('/api/prompts').then((r) => r.prompts),
  get: (id: number) => req<PromptDTO>(`/api/prompts/${id}`),
  create: (input: PromptInput) => req<PromptDTO>('/api/prompts', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<PromptInput> & { systemPrompt: string }) =>
    req<PromptDTO>(`/api/prompts/${id}`, { method: 'PUT', body: JSON.stringify(input) }),
  remove: (id: number) => req<{ ok: boolean }>(`/api/prompts/${id}`, { method: 'DELETE' }),
  clone: (id: number, scenario?: string) =>
    req<PromptDTO>(`/api/prompts/${id}/clone`, { method: 'POST', body: JSON.stringify({ scenario }) }),
  publish: (id: number) => req<PromptDTO>(`/api/prompts/${id}/publish`, { method: 'POST' }),
  rollback: (id: number, version: number) =>
    req<PromptDTO>(`/api/prompts/${id}/rollback`, { method: 'POST', body: JSON.stringify({ version }) }),
  test: (id: number, variables: Record<string, string>) =>
    req<{ rendered: string; reply: string; variables: string[] }>(`/api/prompts/${id}/test`, {
      method: 'POST',
      body: JSON.stringify({ variables }),
    }),
  versions: (id: number) =>
    req<{ versions: VersionDTO[] }>(`/api/prompts/${id}/versions`).then((r) => r.versions),
};
