/** 前端 DID 路由 API 客户端。 */
export interface RouteDTO {
  id: number;
  did: string;
  didPattern: string | null;
  tenantId: string;
  bizType: string;
  scenario: string;
  isActive: boolean;
  description: string | null;
  updateUser: string;
  updateTime: string;
}

export interface RouteInput {
  did: string;
  didPattern?: string | null;
  bizType: string;
  scenario?: string;
  isActive?: boolean;
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

export const routesApi = {
  list: () => req<{ routes: RouteDTO[] }>('/api/inbound-routes').then((r) => r.routes),
  create: (input: RouteInput) =>
    req<RouteDTO>('/api/inbound-routes', { method: 'POST', body: JSON.stringify(input) }),
  update: (id: number, input: Partial<RouteInput>) =>
    req<RouteDTO>(`/api/inbound-routes/${id}`, { method: 'PUT', body: JSON.stringify(input) }),
  remove: (id: number) => req<{ ok: boolean }>(`/api/inbound-routes/${id}`, { method: 'DELETE' }),
};
