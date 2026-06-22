'use client';

/** 租户管理:列表 + 新建/停用/删除 + 用户归属分配(展开行)。仅 platform_admin 可达(page 层 + API 双守卫)。 */
import { Fragment, useEffect, useState, useCallback } from 'react';
import { Plus, Building2, Users } from 'lucide-react';

interface Tenant {
  id: string;
  name: string;
  status: string;
  quota: Record<string, unknown>;
  description: string | null;
}
interface UserRow {
  id: string;
  email: string;
  name: string;
  role: string;
  tenantId: string;
  tenants: { tenantId: string; isPrimary: boolean }[];
}

export default function TenantsManager() {
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [users, setUsers] = useState<UserRow[]>([]);
  const [form, setForm] = useState({ id: '', name: '', description: '' });
  const [expanded, setExpanded] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const flash = (type: 'ok' | 'err', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async () => {
    const [t, u] = await Promise.all([
      fetch('/api/tenants').then((r) => r.json()),
      fetch('/api/users').then((r) => r.json()),
    ]);
    setTenants(t.tenants ?? []);
    setUsers(u.users ?? []);
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const createTenant = async () => {
    if (!form.id || !form.name) return flash('err', 'id / name 必填');
    const r = await fetch('/api/tenants', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(form),
    });
    if (r.ok) {
      flash('ok', `已新增租户 ${form.name}`);
      setForm({ id: '', name: '', description: '' });
      load();
    } else {
      const e = await r.json().catch(() => ({}));
      flash('err', e.error ?? '新增失败');
    }
  };

  const toggleStatus = async (t: Tenant) => {
    const next = t.status === 'active' ? 'disabled' : 'active';
    const r = await fetch(`/api/tenants/${t.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: next }),
    });
    if (r.ok) {
      flash('ok', `${t.name} 已${next === 'active' ? '启用' : '停用'}`);
      load();
    }
  };

  const deleteTenant = async (t: Tenant) => {
    if (!confirm(`确认删除租户 ${t.name}?`)) return;
    const r = await fetch(`/api/tenants/${t.id}`, { method: 'DELETE' });
    if (r.ok) {
      flash('ok', `已删除 ${t.name}`);
      load();
    } else {
      const e = await r.json().catch(() => ({}));
      flash('err', e.error ?? '删除失败');
    }
  };

  const assignTenant = async (
    userId: string,
    tenantId: string,
    action: 'add' | 'remove' | 'setPrimary',
  ) => {
    const r = await fetch(`/api/users/${userId}/tenants`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenantId, action }),
    });
    if (r.ok) {
      flash('ok', '归属已更新');
      load();
    } else {
      const e = await r.json().catch(() => ({}));
      flash('err', e.error ?? '操作失败');
    }
  };

  return (
    <div className="space-y-4">
      <h2 className="text-base font-bold text-slate-800 flex items-center gap-2">
        <Building2 className="w-4 h-4" />
        租户管理
      </h2>
      <p className="text-xs text-slate-500">
        租户是隔离键的一等公民。新建租户后,给用户分配归属,用户登录后即可在顶栏切换活跃租户。
      </p>

      {toast && (
        <div
          className={`text-xs px-3 py-2 rounded ${
            toast.type === 'ok' ? 'bg-emerald-50 text-emerald-700' : 'bg-rose-50 text-rose-700'
          }`}
        >
          {toast.msg}
        </div>
      )}

      {/* 新建租户表单 */}
      <div className="bg-white border border-slate-200 rounded-lg p-4 space-y-2">
        <h3 className="text-sm font-bold text-slate-700">新增租户</h3>
        <div className="flex gap-2 flex-wrap items-center">
          <input
            placeholder="id (kebab-case)"
            value={form.id}
            onChange={(e) => setForm({ ...form, id: e.target.value })}
            className="border border-slate-300 rounded px-2 py-1 text-xs w-40"
          />
          <input
            placeholder="名称"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            className="border border-slate-300 rounded px-2 py-1 text-xs w-32"
          />
          <input
            placeholder="描述(可选)"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            className="border border-slate-300 rounded px-2 py-1 text-xs flex-1 min-w-[200px]"
          />
          <button
            onClick={createTenant}
            className="bg-indigo-600 text-white px-3 py-1 rounded text-xs flex items-center gap-1 hover:bg-indigo-700"
          >
            <Plus className="w-3 h-3" />
            新建
          </button>
        </div>
      </div>

      {/* 租户列表 + 用户归属分配 */}
      <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-slate-50 text-slate-500">
            <tr>
              <th className="text-left p-3 font-semibold">ID</th>
              <th className="text-left p-3 font-semibold">名称</th>
              <th className="text-left p-3 font-semibold">状态</th>
              <th className="text-left p-3 font-semibold">用户归属</th>
              <th className="text-left p-3 font-semibold">操作</th>
            </tr>
          </thead>
          <tbody>
            {tenants.map((t) => {
              const tenantUsers = users.filter((u) => u.tenants.some((ut) => ut.tenantId === t.id));
              return (
                <Fragment key={t.id}>
                  <tr className="border-t border-slate-100">
                    <td className="p-3 font-mono text-slate-600">{t.id}</td>
                    <td className="p-3 font-medium">{t.name}</td>
                    <td className="p-3">
                      <span
                        className={`px-2 py-0.5 rounded ${
                          t.status === 'active'
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-slate-200 text-slate-500'
                        }`}
                      >
                        {t.status}
                      </span>
                    </td>
                    <td className="p-3 text-slate-600">{tenantUsers.length} 人</td>
                    <td className="p-3 flex gap-3">
                      <button
                        onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                        className="text-indigo-600 hover:underline flex items-center gap-0.5"
                      >
                        <Users className="w-3 h-3" />
                        分配
                      </button>
                      <button onClick={() => toggleStatus(t)} className="text-slate-600 hover:underline">
                        {t.status === 'active' ? '停用' : '启用'}
                      </button>
                      <button onClick={() => deleteTenant(t)} className="text-rose-600 hover:underline">
                        删除
                      </button>
                    </td>
                  </tr>
                  {expanded === t.id && (
                    <tr className="border-t border-slate-100 bg-slate-50">
                      <td colSpan={5} className="p-3">
                        <p className="text-[11px] text-slate-500 mb-2">
                          勾选归属此租户的用户;★ 为主租户(每用户仅一主,影响登录默认活跃租户)
                        </p>
                        <div className="space-y-1">
                          {users.map((u) => {
                            const ut = u.tenants.find((x) => x.tenantId === t.id);
                            const assigned = !!ut;
                            return (
                              <div key={u.id} className="flex items-center gap-3 text-xs py-0.5">
                                <input
                                  type="checkbox"
                                  checked={assigned}
                                  onChange={() => assignTenant(u.id, t.id, assigned ? 'remove' : 'add')}
                                />
                                <span className="flex-1">
                                  {u.name} <span className="text-slate-400">{u.email}</span>
                                </span>
                                {assigned && (
                                  <button
                                    onClick={() => assignTenant(u.id, t.id, 'setPrimary')}
                                    className={`px-2 py-0.5 rounded ${
                                      ut.isPrimary
                                        ? 'bg-amber-100 text-amber-700'
                                        : 'bg-slate-200 text-slate-500 hover:bg-slate-300'
                                    }`}
                                  >
                                    {ut.isPrimary ? '★ 主租户' : '设为主'}
                                  </button>
                                )}
                              </div>
                            );
                          })}
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {tenants.length === 0 && (
              <tr>
                <td colSpan={5} className="p-6 text-center text-slate-400 text-xs">
                  暂无租户
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
