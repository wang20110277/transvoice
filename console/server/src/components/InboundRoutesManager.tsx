'use client';

import { useCallback, useEffect, useState } from 'react';
import { Plus, Edit3, Trash2, RefreshCw, CheckCircle2, AlertCircle, Route } from 'lucide-react';
import { routesApi, type RouteDTO, type RouteInput } from '@/lib/routes-api';

const BIZ_TYPES = ['customer_service', 'collection', 'marketing'];

export default function InboundRoutesManager({ tenantId }: { tenantId: string }) {
  const [routes, setRoutes] = useState<RouteDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const [editing, setEditing] = useState<RouteDTO | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<RouteInput>({
    did: '',
    didPattern: '',
    bizType: 'marketing',
    scenario: 'default',
    isActive: true,
    description: '',
  });

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setRoutes(await routesApi.list());
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const openCreate = () => {
    setEditing(null);
    setForm({ did: '', didPattern: '', bizType: 'marketing', scenario: 'default', isActive: true, description: '' });
    setShowForm(true);
  };

  const openEdit = (r: RouteDTO) => {
    setEditing(r);
    setForm({
      did: r.did,
      didPattern: r.didPattern ?? '',
      bizType: r.bizType,
      scenario: r.scenario,
      isActive: r.isActive,
      description: r.description ?? '',
    });
    setShowForm(true);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.did.trim() || !form.bizType) return;
    setBusy(true);
    try {
      const payload: RouteInput = {
        ...form,
        didPattern: form.didPattern?.trim() || null,
        description: form.description?.trim() || null,
      };
      if (editing) {
        await routesApi.update(editing.id, payload);
        flash('ok', `已更新 DID ${form.did}`);
      } else {
        await routesApi.create(payload);
        flash('ok', `已新增 DID ${form.did}(呼入即时生效)`);
      }
      setShowForm(false);
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (r: RouteDTO) => {
    if (!window.confirm(`确认删除 DID ${r.did} 的路由?`)) return;
    setBusy(true);
    try {
      await routesApi.remove(r.id);
      flash('ok', `已删除 DID ${r.did}`);
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleActive = async (r: RouteDTO) => {
    setBusy(true);
    try {
      await routesApi.update(r.id, { isActive: !r.isActive });
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <div>
          <h2 className="text-base font-bold text-slate-800">呼入路由</h2>
          <p className="text-xs text-slate-500 mt-1">
            被叫号(DID)→ (tenant_id, biz_type, scenario)。dialplan 已 catch-all,新增/修改路由即时生效,无需改 FreeSWITCH。
          </p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center gap-1 px-3.5 py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          <Plus className="w-4 h-4" /> 新增路由
        </button>
      </div>

      {toast && (
        <div
          className={`flex items-center gap-2 text-xs p-2.5 rounded-lg border ${
            toast.kind === 'ok' ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-rose-50 border-rose-200 text-rose-700'
          }`}
        >
          {toast.kind === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      {showForm && (
        <form onSubmit={save} className="bg-white p-5 rounded-xl border border-slate-100 shadow-xs space-y-4">
          <div className="flex justify-between items-center border-b border-slate-100 pb-2">
            <h3 className="text-sm font-bold text-slate-800">{editing ? `编辑 DID ${editing.did}` : '新增呼入路由'}</h3>
            <button type="button" onClick={() => setShowForm(false)} className="text-xs text-slate-500 hover:text-slate-800">取消</button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">DID(精确被叫号,全局唯一)</label>
              <input
                value={form.did}
                onChange={(e) => setForm({ ...form, did: e.target.value })}
                placeholder="例:8004"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 font-mono"
                required
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">biz_type</label>
              <select
                value={form.bizType}
                onChange={(e) => setForm({ ...form, bizType: e.target.value })}
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              >
                {BIZ_TYPES.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">scenario</label>
              <input
                value={form.scenario}
                onChange={(e) => setForm({ ...form, scenario: e.target.value })}
                placeholder="default / activation"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 font-mono"
              />
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">号段正则(可选,精确号未命中时兜底)</label>
              <input
                value={form.didPattern ?? ''}
                onChange={(e) => setForm({ ...form, didPattern: e.target.value })}
                placeholder="例:^800[1-3]$ (留空则仅精确匹配)"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 font-mono"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">描述</label>
              <input
                value={form.description ?? ''}
                onChange={(e) => setForm({ ...form, description: e.target.value })}
                placeholder="例:营销高意向激活线"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input type="checkbox" checked={form.isActive} onChange={(e) => setForm({ ...form, isActive: e.target.checked })} />
            启用(关闭后该 DID 呼入不命中此路由)
          </label>
          <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
            <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 bg-slate-100 text-slate-600 text-xs rounded-lg hover:bg-slate-200 font-semibold">取消</button>
            <button type="submit" disabled={busy} className="px-4 py-2 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 font-semibold disabled:opacity-50">
              {busy ? '保存中…' : '保存'}
            </button>
          </div>
        </form>
      )}

      <div className="bg-white rounded-xl border border-slate-100 shadow-xs overflow-hidden">
        <div className="flex justify-between items-center px-4 py-3 border-b border-slate-100">
          <span className="text-xs font-bold text-slate-700">路由列表 ({routes.length})</span>
          <button onClick={reload} className="text-slate-400 hover:text-slate-700">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {loading ? (
          <p className="text-slate-400 text-xs text-center py-10">加载中…</p>
        ) : routes.length === 0 ? (
          <p className="text-slate-400 text-xs text-center py-10">本租户暂无呼入路由</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="text-left p-3 font-semibold">DID</th>
                <th className="text-left p-3 font-semibold">号段正则</th>
                <th className="text-left p-3 font-semibold">biz_type</th>
                <th className="text-left p-3 font-semibold">scenario</th>
                <th className="text-left p-3 font-semibold">状态</th>
                <th className="text-left p-3 font-semibold">描述</th>
                <th className="text-right p-3 font-semibold">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {routes.map((r) => (
                <tr key={r.id} className="hover:bg-slate-50/50">
                  <td className="p-3 font-mono font-semibold text-slate-800">{r.did}</td>
                  <td className="p-3 font-mono text-slate-500">{r.didPattern ?? '—'}</td>
                  <td className="p-3 text-slate-600">{r.bizType}</td>
                  <td className="p-3 font-mono text-slate-600">{r.scenario}</td>
                  <td className="p-3">
                    <button
                      onClick={() => toggleActive(r)}
                      disabled={busy}
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        r.isActive ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'
                      } disabled:opacity-50`}
                    >
                      {r.isActive ? '启用' : '停用'}
                    </button>
                  </td>
                  <td className="p-3 text-slate-500 max-w-[200px] truncate">{r.description ?? '—'}</td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => openEdit(r)} disabled={busy} title="编辑" className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded disabled:opacity-40"><Edit3 className="w-3.5 h-3.5" /></button>
                      <button onClick={() => doDelete(r)} disabled={busy} title="删除" className="p-1.5 text-rose-600 hover:bg-rose-50 rounded disabled:opacity-40"><Trash2 className="w-3.5 h-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="bg-blue-50/40 border border-blue-100/60 rounded-lg p-3 text-[11px] text-blue-800 space-y-1">
        <p className="flex items-center gap-1.5 font-semibold"><Route className="w-3.5 h-3.5" /> 呼入解析链路(dialplan 已 catch-all,无需改 FS)</p>
        <p className="font-mono text-[10px]">呼入 → FS 应答(catch-all) → agent-flow 读 Caller-Destination-Number=DID → 查本表(精确号优先,号段兜底) → (tenant_id, biz_type, scenario) → 命中对应 prompt_config</p>
        <p>当前租户:<span className="font-mono">{tenantId}</span>。新增 DID 后,记得在「提示词」建一条同 (biz_type, scenario) 的提示词并发布。</p>
      </div>
    </div>
  );
}
