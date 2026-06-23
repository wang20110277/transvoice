/** 前端外呼任务 API 客户端。 */
export interface CallTaskDTO {
  id: number;
  tenantId: string;
  name: string;
  promptId: number;
  kbIds: unknown[];
  status: string;
  concurrentLimit: number;
  allowedHours: string | null;
  redialStrategy: Record<string, unknown>;
  deptId: string | null;
  description: string | null;
  updateUser: string;
  updateTime: string;
}

export interface CallTaskInput {
  name: string;
  promptId: number;
  kbIds?: unknown[];
  status?: string;
  concurrentLimit?: number;
  allowedHours?: string | null;
  redialStrategy?: Record<string, unknown>;
  deptId?: string | null;
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

export const callTasksApi = {
  list: () => req<{ tasks: CallTaskDTO[] }>('/api/call-tasks').then((r) => r.tasks),
  create: (input: CallTaskInput) =>
    req<CallTaskDTO>('/api/call-tasks', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<CallTaskInput>) =>
    req<CallTaskDTO>(`/api/call-tasks/${id}`, { method: 'PUT', body: JSON.stringify(input) }),
  remove: (id: number) => req<{ ok: boolean }>(`/api/call-tasks/${id}`, { method: 'DELETE' }),
};
