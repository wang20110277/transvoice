/** 前端通话记录 API 客户端。 */
import type { SessionDTO } from './calls-service';

export interface CallTurnClient {
  id: number;
  role: string;
  text: string | null;
  ts: string;
}

export interface CallEventClient {
  id: number;
  eventType: string;
  payload: Record<string, unknown>;
  ts: string;
}

export interface CallArtifactClient {
  id: number;
  kind: string;
  storage: string;
  uri: string;
  sizeBytes: number | null;
  contentType: string | null;
}

export interface CallDetailClient {
  session: SessionDTO;
  turns: CallTurnClient[];
  events: CallEventClient[];
  artifacts: CallArtifactClient[];
}

export interface ListQuery {
  bizType?: string;
  phoneMasked?: string;
  direction?: 'inbound' | 'outbound';
  startFrom?: string;
  startTo?: string;
  page?: number;
  pageSize?: number;
}

/** 非 2xx 响应抛出携带 status 的错误，供调用方按状态码分支（如手动归档 409/410/502）。 */
export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = 'HttpError';
  }
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { ...init, headers: { ...(init?.headers ?? {}) } });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new HttpError(res.status, (data as { error?: string }).error ?? `HTTP ${res.status}`);
  return data as T;
}

function qs(q: ListQuery): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v) p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
}

export const callsApi = {
  list: (q: ListQuery = {}) => req<{ calls: SessionDTO[]; total: number }>(`/api/calls${qs(q)}`),
  detail: (id: number) => req<CallDetailClient>(`/api/calls/${id}`),
  recordingUrl: (id: number) => req<{ url: string; expiresIn: number }>(`/api/calls/${id}/recording-url`),
  archiveRecording: (id: number) =>
    req<{ objectKey?: string; error?: string }>(`/api/calls/${id}/archive-recording`, { method: 'POST' }),
};
