/** 前端外呼号码清单 API 客户端。 */
export interface CallTargetDTO {
  id: number;
  taskId: number;
  phoneMasked: string | null;
  userKey: string;
  status: string;
  attemptCount: number;
  maxAttempts: number;
  nextAttemptTs: string | null;
  lastHangupCause: string | null;
  updateTime: string;
}

export interface CallTargetProgress {
  total: number;
  pending: number;
  dialing: number;
  answered: number;
  noAnswer: number;
  failed: number;
  done: number;
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

export const callTargetsApi = {
  list: (taskId: number) =>
    req<{ targets: CallTargetDTO[] }>(`/api/call-tasks/${taskId}/targets`).then((r) => r.targets),
  create: (taskId: number, phone: string, maxAttempts = 1) =>
    req<CallTargetDTO>(`/api/call-tasks/${taskId}/targets`, {
      method: 'POST', body: JSON.stringify({ phone, maxAttempts }),
    }),
  uploadCsv: (taskId: number, csv: string, maxAttempts = 1) =>
    req<{ inserted: number; skipped: number }>(`/api/call-tasks/${taskId}/targets`, {
      method: 'POST', body: JSON.stringify({ csv, maxAttempts }),
    }),
  remove: (taskId: number, targetId: number) =>
    req<{ ok: boolean }>(`/api/call-tasks/${taskId}/targets/${targetId}`, { method: 'DELETE' }),
  progress: (taskId: number) =>
    req<CallTargetProgress>(`/api/call-tasks/${taskId}/progress`),
};
