'use client';

import { useCallback, useEffect, useState } from 'react';
import { Plus, Edit3, Trash2, RefreshCw, CheckCircle2, AlertCircle, PhoneCall } from 'lucide-react';
import { callTasksApi, type CallTaskDTO, type CallTaskInput } from '@/lib/call-tasks-api';
import { promptsApi, type PromptDTO } from '@/lib/prompts-api';

const STATUSES = ['idle', 'running', 'paused', 'completed'];

const emptyForm: CallTaskInput = {
  name: '',
  promptId: 0,
  concurrentLimit: 1,
  allowedHours: '',
  deptId: '',
  description: '',
  status: 'idle',
};

export default function CallTasksManager({ tenantId }: { tenantId: string }) {
  const [tasks, setTasks] = useState<CallTaskDTO[]>([]);
  const [prompts, setPrompts] = useState<PromptDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const [editing, setEditing] = useState<CallTaskDTO | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<CallTaskInput>(emptyForm);

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [t, p] = await Promise.all([callTasksApi.list(), promptsApi.list().catch(() => [] as PromptDTO[])]);
      setTasks(t);
      setPrompts(p);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const promptLabel = (id: number) => {
    const p = prompts.find((x) => x.id === id);
    return p ? `${p.title} (${p.bizType}/${p.scenario})` : `#${id}`;
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...emptyForm, promptId: prompts[0]?.id ?? 0 });
    setShowForm(true);
  };

  const openEdit = (t: CallTaskDTO) => {
    setEditing(t);
    setForm({
      name: t.name,
      promptId: t.promptId,
      concurrentLimit: t.concurrentLimit,
      allowedHours: t.allowedHours ?? '',
      deptId: t.deptId ?? '',
      description: t.description ?? '',
      status: t.status,
    });
    setShowForm(true);
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim() || !form.promptId) return;
    setBusy(true);
    try {
      const payload: CallTaskInput = {
        ...form,
        allowedHours: form.allowedHours?.trim() || null,
        deptId: form.deptId?.trim() || null,
        description: form.description?.trim() || null,
      };
      if (editing) {
        await callTasksApi.update(editing.id, payload);
        flash('ok', `已更新任务 ${form.name}`);
      } else {
        await callTasksApi.create(payload);
        flash('ok', `已新增任务 ${form.name}（定义层，不发起外呼）`);
      }
      setShowForm(false);
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (t: CallTaskDTO) => {
    if (!window.confirm(`确认删除外呼任务「${t.name}」？`)) return;
    setBusy(true);
    try {
      await callTasksApi.remove(t.id);
      flash('ok', `已删除任务 ${t.name}`);
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
          <h2 className="text-base font-bold text-slate-800">外呼任务（定义层）</h2>
          <p className="text-xs text-slate-500 mt-1">
            绑定提示词 + 策略参数，落 call_task 表。本期仅定义，不发起 originate/调度/重拨（执行属后续变更）。
          </p>
        </div>
        <button
          onClick={openCreate}
          disabled={prompts.length === 0}
          title={prompts.length === 0 ? '先在「提示词管理」创建并发布至少一条提示词' : '新增任务'}
          className="flex items-center gap-1 px-3.5 py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white disabled:opacity-40"
        >
          <Plus className="w-4 h-4" /> 新增任务
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
            <h3 className="text-sm font-bold text-slate-800">{editing ? `编辑任务「${editing.name}」` : '新增外呼任务'}</h3>
            <button type="button" onClick={() => setShowForm(false)} className="text-xs text-slate-500 hover:text-slate-800">取消</button>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">任务名</label>
              <input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例：高意向客户激活-首批"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
                required
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">绑定提示词（同租户）</label>
              <select
                value={form.promptId}
                onChange={(e) => setForm({ ...form, promptId: Number(e.target.value) })}
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
                required
              >
                {prompts.length === 0 ? (
                  <option value={0}>（本租户暂无提示词）</option>
                ) : (
                  prompts.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.title} ({p.bizType}/{p.scenario})
                    </option>
                  ))
                )}
              </select>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">并发上限</label>
              <input
                type="number"
                min={1}
                value={form.concurrentLimit}
                onChange={(e) => setForm({ ...form, concurrentLimit: Number(e.target.value) })}
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">允许时段</label>
              <input
                value={form.allowedHours ?? ''}
                onChange={(e) => setForm({ ...form, allowedHours: e.target.value })}
                placeholder="例：09:00-21:00（声明性，本期不执行）"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-slate-500 font-semibold">dept_id（映射 biz_type）</label>
              <input
                value={form.deptId ?? ''}
                onChange={(e) => setForm({ ...form, deptId: e.target.value })}
                placeholder="可选"
                className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-slate-500 font-semibold">状态（本期仅存储，无执行器驱动流转）</label>
            <select
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
              className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 md:w-1/3"
            >
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-slate-500 font-semibold">描述</label>
            <input
              value={form.description ?? ''}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              placeholder="可选"
              className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
            <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 bg-slate-100 text-slate-600 text-xs rounded-lg hover:bg-slate-200 font-semibold">取消</button>
            <button type="submit" disabled={busy || !form.promptId} className="px-4 py-2 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 font-semibold disabled:opacity-50">
              {busy ? '保存中…' : '保存'}
            </button>
          </div>
        </form>
      )}

      <div className="bg-white rounded-xl border border-slate-100 shadow-xs overflow-hidden">
        <div className="flex justify-between items-center px-4 py-3 border-b border-slate-100">
          <span className="text-xs font-bold text-slate-700">任务列表 ({tasks.length})</span>
          <button onClick={reload} className="text-slate-400 hover:text-slate-700">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {loading ? (
          <p className="text-slate-400 text-xs text-center py-10">加载中…</p>
        ) : tasks.length === 0 ? (
          <p className="text-slate-400 text-xs text-center py-10">本租户暂无外呼任务</p>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="text-left p-3 font-semibold">任务名</th>
                <th className="text-left p-3 font-semibold">绑定提示词</th>
                <th className="text-left p-3 font-semibold">状态</th>
                <th className="text-left p-3 font-semibold">并发</th>
                <th className="text-left p-3 font-semibold">时段</th>
                <th className="text-left p-3 font-semibold">描述</th>
                <th className="text-right p-3 font-semibold">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {tasks.map((t) => (
                <tr key={t.id} className="hover:bg-slate-50/50">
                  <td className="p-3 font-semibold text-slate-800">{t.name}</td>
                  <td className="p-3 text-slate-600">{promptLabel(t.promptId)}</td>
                  <td className="p-3">
                    <span className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                      t.status === 'running' ? 'bg-emerald-100 text-emerald-700' :
                      t.status === 'paused' ? 'bg-amber-100 text-amber-700' :
                      t.status === 'completed' ? 'bg-slate-200 text-slate-600' :
                      'bg-slate-100 text-slate-500'
                    }`}>{t.status}</span>
                  </td>
                  <td className="p-3 text-slate-600">{t.concurrentLimit}</td>
                  <td className="p-3 font-mono text-slate-500">{t.allowedHours ?? '—'}</td>
                  <td className="p-3 text-slate-500 max-w-[200px] truncate">{t.description ?? '—'}</td>
                  <td className="p-3">
                    <div className="flex justify-end gap-1">
                      <button onClick={() => openEdit(t)} disabled={busy} title="编辑" className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded disabled:opacity-40"><Edit3 className="w-3.5 h-3.5" /></button>
                      <button onClick={() => doDelete(t)} disabled={busy} title="删除" className="p-1.5 text-rose-600 hover:bg-rose-50 rounded disabled:opacity-40"><Trash2 className="w-3.5 h-3.5" /></button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="bg-blue-50/40 border border-blue-100/60 rounded-lg p-3 text-[11px] text-blue-800 space-y-1">
        <p className="flex items-center gap-1.5 font-semibold"><PhoneCall className="w-3.5 h-3.5" /> 外呼任务定义层（本期不含执行）</p>
        <p>当前租户：<span className="font-mono">{tenantId}</span>。任务绑定的 promptId MUST 属于本租户（跨租户绑定会被拒绝）。</p>
        <p>策略字段（并发/时段/重拨）仅为声明性存储，originate/调度/重拨执行器属独立后续变更。</p>
      </div>
    </div>
  );
}
