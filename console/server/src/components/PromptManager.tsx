'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Plus,
  Edit3,
  Trash2,
  Copy,
  History,
  Terminal,
  RefreshCw,
  Layers,
  Send,
  RotateCcw,
  CheckCircle2,
  AlertCircle,
} from 'lucide-react';
import { promptsApi, type PromptDTO, type VersionDTO, type PromptInput } from '@/lib/prompts-api';
import { extractVariables } from '@/lib/prompt-template';

const BIZ_TYPES = [
  { value: 'customer_service', label: '客服 (customer_service)' },
  { value: 'collection', label: '催收 (collection)' },
  { value: 'marketing', label: '营销 (marketing)' },
];

type Mode = 'view' | 'create' | 'edit' | 'history';

export default function PromptManager() {
  const [prompts, setPrompts] = useState<PromptDTO[]>([]);
  const [selected, setSelected] = useState<PromptDTO | null>(null);
  const [mode, setMode] = useState<Mode>('view');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);

  // 表单
  const [form, setForm] = useState<PromptInput>({
    title: '',
    bizType: 'customer_service',
    scenario: 'default',
    systemPrompt: '',
    category: '通用',
    description: '',
  });
  const [editingId, setEditingId] = useState<number | null>(null);

  // 联调沙箱
  const [sandboxVars, setSandboxVars] = useState<Record<string, string>>({});
  const [sandboxOut, setSandboxOut] = useState<{ rendered: string; reply: string } | null>(null);
  const [sandboxLoading, setSandboxLoading] = useState(false);

  // 历史
  const [versions, setVersions] = useState<VersionDTO[]>([]);

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await promptsApi.list();
      setPrompts(rows);
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
    setEditingId(null);
    setForm({ title: '', bizType: 'customer_service', scenario: 'default', systemPrompt: '', category: '通用', description: '' });
    setMode('create');
  };

  const openEdit = (p: PromptDTO) => {
    setEditingId(p.id);
    setForm({
      title: p.title,
      bizType: p.bizType,
      scenario: p.scenario,
      systemPrompt: p.systemPrompt,
      category: p.category,
      description: p.description ?? '',
    });
    setMode('edit');
  };

  const openHistory = async (p: PromptDTO) => {
    setSelected(p);
    setMode('history');
    try {
      setVersions(await promptsApi.versions(p.id));
    } catch (e) {
      flash('err', (e as Error).message);
    }
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.title.trim() || !form.systemPrompt.trim()) return;
    setBusy(true);
    try {
      if (editingId) {
        const updated = await promptsApi.update(editingId, { systemPrompt: form.systemPrompt, title: form.title, category: form.category, description: form.description || null });
        setSelected(updated);
        flash('ok', `已保存新版本 v${updated.version}`);
      } else {
        const created = await promptsApi.create(form);
        setSelected(created);
        flash('ok', `已创建草稿(发布后生效)`);
      }
      setMode('view');
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doPublish = async (p: PromptDTO) => {
    setBusy(true);
    try {
      const updated = await promptsApi.publish(p.id);
      setSelected(updated);
      flash('ok', '已发布,缓存已失效(零延迟生效)');
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doClone = async (p: PromptDTO) => {
    const scenario = window.prompt('克隆到新 scenario(同 biz_type 下需唯一):', `${p.scenario}-copy`);
    if (!scenario) return;
    setBusy(true);
    try {
      const cloned = await promptsApi.clone(p.id, scenario);
      flash('ok', `已克隆到 ${p.bizType}/${scenario}`);
      setSelected(cloned);
      setMode('view');
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async (p: PromptDTO) => {
    if (!window.confirm(`确认删除《${p.title}》(${p.bizType}/${p.scenario})?`)) return;
    setBusy(true);
    try {
      await promptsApi.remove(p.id);
      setSelected(null);
      flash('ok', '已删除');
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const doRollback = async (p: PromptDTO, v: number) => {
    setBusy(true);
    try {
      const updated = await promptsApi.rollback(p.id, v);
      setSelected(updated);
      flash('ok', `已回滚至 v${v}(现为 v${updated.version})`);
      setVersions(await promptsApi.versions(p.id));
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const launchSandbox = (p: PromptDTO) => {
    const init: Record<string, string> = {};
    p.variables.forEach((v) => (init[v] = ''));
    setSandboxVars(init);
    setSandboxOut(null);
  };

  const runTest = async (p: PromptDTO) => {
    setSandboxLoading(true);
    try {
      const out = await promptsApi.test(p.id, sandboxVars);
      setSandboxOut({ rendered: out.rendered, reply: out.reply });
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setSandboxLoading(false);
    }
  };

  const formVars = extractVariables(form.systemPrompt);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <div>
          <h2 className="text-base font-bold text-slate-800">提示词配置管理</h2>
          <p className="text-xs text-slate-500 mt-1">
            三维度 (tenant_id, biz_type, scenario) 唯一。发布后清 Redis 缓存零延迟生效,呼入 agent-flow 即时取用。
          </p>
        </div>
        <button
          onClick={openCreate}
          className="flex items-center gap-1 px-3.5 py-2 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white"
        >
          <Plus className="w-4 h-4" /> 新建提示词
        </button>
      </div>

      {toast && (
        <div
          className={`flex items-center gap-2 text-xs p-2.5 rounded-lg border ${
            toast.kind === 'ok'
              ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
              : 'bg-rose-50 border-rose-200 text-rose-700'
          }`}
        >
          {toast.kind === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* 左:列表 */}
        <div className="lg:col-span-4 bg-white rounded-xl border border-slate-100 p-3 space-y-2 shadow-xs h-[640px] overflow-y-auto">
          <div className="flex justify-between items-center border-b border-slate-100 pb-2 mb-1">
            <span className="text-xs font-bold text-slate-700">模板目录 ({prompts.length})</span>
            <button onClick={reload} className="text-slate-400 hover:text-slate-700">
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          {loading ? (
            <p className="text-slate-400 text-xs text-center py-10">加载中…</p>
          ) : prompts.length === 0 ? (
            <p className="text-slate-400 text-xs text-center py-10">本租户暂无提示词</p>
          ) : (
            prompts.map((p) => {
              const isSel = selected?.id === p.id;
              return (
                <div
                  key={p.id}
                  onClick={() => {
                    setSelected(p);
                    setMode('view');
                  }}
                  className={`p-3 rounded-lg border cursor-pointer transition-all ${
                    isSel ? 'border-indigo-600 bg-indigo-50/40' : 'border-slate-150 hover:bg-slate-50'
                  }`}
                >
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold bg-slate-100 text-slate-600 font-mono">
                      {p.bizType}/{p.scenario}
                    </span>
                    <div className="flex items-center gap-1">
                      {p.isActive ? (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-semibold">已发布</span>
                      ) : (
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-semibold">草稿</span>
                      )}
                      <span className="text-[10px] text-slate-400 font-mono">v{p.version}</span>
                    </div>
                  </div>
                  <h3 className="text-xs font-bold text-slate-800 mt-1 line-clamp-1">{p.title}</h3>
                  <p className="text-[11px] text-slate-500 line-clamp-2 mt-0.5 leading-relaxed">{p.systemPrompt}</p>
                </div>
              );
            })
          )}
        </div>

        {/* 右:详情/表单/历史/沙箱 */}
        <div className="lg:col-span-8 bg-white rounded-xl border border-slate-100 p-5 shadow-xs min-h-[640px]">
          {mode === 'create' || mode === 'edit' ? (
            <form onSubmit={save} className="space-y-4">
              <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                <h3 className="text-sm font-bold text-slate-800">{mode === 'edit' ? '编辑提示词' : '新建提示词'}</h3>
                <button type="button" onClick={() => setMode('view')} className="text-xs text-slate-500 hover:text-slate-800">取消</button>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-xs text-slate-500 font-semibold">标题</label>
                  <input
                    value={form.title}
                    onChange={(e) => setForm({ ...form, title: e.target.value })}
                    placeholder="例:逾期还款提醒-温和口径"
                    className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
                    required
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-slate-500 font-semibold">分类(标签)</label>
                  <input
                    value={form.category}
                    onChange={(e) => setForm({ ...form, category: e.target.value })}
                    placeholder="通用 / 温和催收 / 意向激活"
                    className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-slate-500 font-semibold">biz_type(业务场景,身份键)</label>
                  <select
                    value={form.bizType}
                    onChange={(e) => setForm({ ...form, bizType: e.target.value })}
                    disabled={mode === 'edit'}
                    className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 disabled:opacity-60"
                  >
                    {BIZ_TYPES.map((b) => (
                      <option key={b.value} value={b.value}>{b.label}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-slate-500 font-semibold">scenario(话术场景,身份键)</label>
                  <input
                    value={form.scenario}
                    onChange={(e) => setForm({ ...form, scenario: e.target.value })}
                    disabled={mode === 'edit'}
                    placeholder="default / gentle_collection"
                    className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 disabled:opacity-60 font-mono"
                  />
                </div>
              </div>

              <div className="space-y-1">
                <div className="flex justify-between text-xs text-slate-500 font-semibold">
                  <span>系统提示词正文(含 {'{变量}'} 占位符)</span>
                  <span className="text-[10px] text-emerald-600">{'{name}'} 自动识别为变量</span>
                </div>
                <textarea
                  value={form.systemPrompt}
                  onChange={(e) => setForm({ ...form, systemPrompt: e.target.value })}
                  rows={12}
                  className="w-full text-xs font-mono p-3 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 resize-none"
                  required
                />
              </div>

              {formVars.length > 0 && (
                <div className="space-y-1">
                  <span className="text-[11px] text-slate-500 font-medium">已识别变量(保存时服务端正则复核):</span>
                  <div className="flex flex-wrap gap-1.5">
                    {formVars.map((v) => (
                      <span key={v} className="bg-amber-50 text-amber-800 text-[10px] font-mono border border-amber-200/50 px-2 py-0.5 rounded">
                        {`{${v}}`}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              <div className="pt-3 border-t border-slate-100 flex justify-end gap-2">
                <button type="button" onClick={() => setMode('view')} className="px-4 py-2 bg-slate-100 text-slate-600 text-xs rounded-lg hover:bg-slate-200 font-semibold">取消</button>
                <button type="submit" disabled={busy} className="px-4 py-2 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 font-semibold disabled:opacity-50">
                  {busy ? '保存中…' : '保存(自增版本号)'}
                </button>
              </div>
            </form>
          ) : mode === 'history' && selected ? (
            <div className="space-y-4">
              <button onClick={() => setMode('view')} className="text-xs text-slate-600 hover:text-indigo-600 font-medium">← 返回详情</button>
              <h3 className="text-sm font-bold text-slate-800">《{selected.title}》版本历史</h3>
              {versions.length === 0 ? (
                <p className="text-slate-400 text-xs py-10 text-center">暂无历史快照</p>
              ) : (
                <div className="border border-slate-150 rounded-lg divide-y divide-slate-100 overflow-hidden">
                  {versions.map((v) => (
                    <div key={v.id} className="p-3 bg-slate-50/50 space-y-2">
                      <div className="flex justify-between items-center text-[10px] text-slate-400">
                        <span>版本 <strong className="text-slate-700 font-mono">v{v.version}</strong> · {v.updateUser}</span>
                        <span>{new Date(v.updateTime).toLocaleString('zh-CN')}</span>
                      </div>
                      <p className="bg-white p-2 rounded border border-slate-100 text-slate-600 font-mono text-[11px] max-h-24 overflow-y-auto whitespace-pre-wrap">{v.systemPrompt}</p>
                      <div className="flex justify-end">
                        <button
                          onClick={() => doRollback(selected, v.version)}
                          disabled={busy || v.version === selected.version}
                          className="bg-indigo-50 border border-indigo-200 text-indigo-700 text-[10px] px-2.5 py-1 rounded hover:bg-indigo-100 font-medium disabled:opacity-40"
                        >
                          <RotateCcw className="w-3 h-3 inline mr-1" />
                          回滚到此版本
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : selected ? (
            <div className="space-y-5">
              <div className="flex justify-between items-start border-b border-slate-100 pb-3">
                <div>
                  <span className="text-[10px] px-2 py-0.5 rounded font-semibold bg-slate-100 text-slate-600 font-mono">{selected.bizType}/{selected.scenario}</span>
                  {selected.isActive ? (
                    <span className="ml-1 text-[10px] px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 font-semibold">已发布</span>
                  ) : (
                    <span className="ml-1 text-[10px] px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 font-semibold">草稿(未发布)</span>
                  )}
                  <h3 className="text-sm font-bold text-slate-800 mt-2">{selected.title}</h3>
                </div>
                <div className="flex gap-1.5">
                  <button onClick={() => doClone(selected)} disabled={busy} title="克隆" className="p-2 border border-slate-150 rounded-lg hover:bg-slate-50 text-slate-600 disabled:opacity-40"><Copy className="w-3.5 h-3.5" /></button>
                  <button onClick={() => openHistory(selected)} title="版本历史" className="p-2 border border-slate-150 rounded-lg hover:bg-slate-50 text-slate-600"><History className="w-3.5 h-3.5" /></button>
                  <button onClick={() => openEdit(selected)} title="编辑" className="p-2 bg-indigo-50 text-indigo-700 border border-indigo-150 rounded-lg hover:bg-indigo-100"><Edit3 className="w-3.5 h-3.5" /></button>
                  <button onClick={() => doDelete(selected)} disabled={busy} title="删除" className="p-2 border border-rose-150 text-rose-600 rounded-lg hover:bg-rose-50 disabled:opacity-40"><Trash2 className="w-3.5 h-3.5" /></button>
                </div>
              </div>

              <div className="bg-slate-50 border border-slate-200 p-4 rounded-xl space-y-3">
                <div className="flex justify-between text-xs text-slate-400 font-mono">
                  <span>版本 v{selected.version} · {selected.updateUser}</span>
                  <span>{new Date(selected.updateTime).toLocaleString('zh-CN')}</span>
                </div>
                <p className="text-xs font-mono text-slate-700 leading-relaxed whitespace-pre-wrap">{selected.systemPrompt}</p>
              </div>

              {selected.variables.length > 0 && (
                <div className="p-3 bg-blue-50/40 rounded-lg border border-blue-100/60">
                  <span className="text-[11px] text-blue-800 font-semibold block mb-1.5">运行时变量(呼入时由 MCP 身份/记忆/call_task.vars 渲染):</span>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.variables.map((v) => (
                      <span key={v} className="text-[10px] font-mono bg-blue-100/60 border border-blue-200 text-blue-800 px-2 py-0.5 rounded">{`{${v}}`}</span>
                    ))}
                  </div>
                </div>
              )}

              {!selected.isActive && (
                <button
                  onClick={() => doPublish(selected)}
                  disabled={busy}
                  className="w-full flex items-center justify-center gap-2 bg-emerald-600 text-white p-3 rounded-xl text-xs font-bold hover:bg-emerald-700 disabled:opacity-50"
                >
                  <Send className="w-4 h-4" /> 发布(置 is_active=true + 清 Redis 缓存)
                </button>
              )}

              <button
                onClick={() => launchSandbox(selected)}
                className="w-full flex items-center justify-center gap-2 bg-slate-900 text-white p-3 rounded-xl text-xs font-bold hover:bg-indigo-950"
              >
                <Terminal className="w-4 h-4" /> 联调沙箱(渲染变量 + 调 ollama qwen3)
              </button>

              {/* 沙箱面板 */}
              {selected.variables.length > 0 && sandboxOut !== null && (
                <div className="border border-slate-150 rounded-lg p-4 space-y-3 bg-slate-50/30">
                  <h4 className="text-xs font-bold text-slate-700">联调沙箱</h4>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {selected.variables.map((v) => (
                      <div key={v} className="space-y-1">
                        <label className="text-[10px] text-slate-500 font-mono">{`{${v}}`}</label>
                        <input
                          value={sandboxVars[v] ?? ''}
                          onChange={(e) => setSandboxVars({ ...sandboxVars, [v]: e.target.value })}
                          className="w-full text-xs p-2 bg-white border border-slate-200 rounded"
                        />
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => runTest(selected)}
                    disabled={sandboxLoading}
                    className="w-full bg-indigo-600 text-white text-xs font-bold p-2.5 rounded-lg hover:bg-indigo-700 disabled:opacity-50"
                  >
                    {sandboxLoading ? '调用 LLM 中…' : '发起联调测试'}
                  </button>
                  {sandboxOut && (
                    <div className="space-y-2">
                      <div>
                        <p className="text-[10px] text-slate-500 font-semibold mb-1">渲染后:</p>
                        <pre className="bg-white border border-slate-100 rounded p-2 text-[11px] font-mono text-slate-600 whitespace-pre-wrap max-h-32 overflow-y-auto">{sandboxOut.rendered}</pre>
                      </div>
                      <div>
                        <p className="text-[10px] text-slate-500 font-semibold mb-1">LLM 回复:</p>
                        <pre className="bg-slate-900 rounded p-2 text-[11px] font-mono text-indigo-300 whitespace-pre-wrap max-h-40 overflow-y-auto">{sandboxOut.reply}</pre>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="h-[600px] flex flex-col items-center justify-center text-center space-y-3 select-none">
              <Layers className="w-12 h-12 text-slate-300 stroke-1" />
              <div className="space-y-1">
                <h4 className="text-slate-800 font-semibold text-xs">从左侧选择一个提示词</h4>
                <p className="text-[11px] text-slate-400 max-w-sm">查看 / 编辑 / 克隆 / 发布 / 回滚 / 联调,数据实时落 PostgreSQL。</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
