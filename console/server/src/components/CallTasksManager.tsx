'use client';

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, Edit3, Trash2, RefreshCw, CheckCircle2, AlertCircle, Play, Pause, ChevronDown, ChevronRight, Upload, FileDown } from 'lucide-react';
import { callTasksApi, type CallTaskDTO, type CallTaskInput } from '@/lib/call-tasks-api';
import { callTargetsApi, type CallTargetDTO, type CallTargetProgress } from '@/lib/call-targets-api';
import { promptsApi, type PromptDTO } from '@/lib/prompts-api';
import { parseImportCsv, extractPlaceholders, IMPORT_TEMPLATE_CSV } from '@/lib/csv-import';

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

  // ── 启停（PATCH status：idle/paused → running 启动；running → paused 暂停）──
  const toggleRun = async (t: CallTaskDTO) => {
    const next = t.status === 'running' ? 'paused' : 'running';
    setBusy(true);
    try {
      await callTasksApi.update(t.id, { status: next });
      flash('ok', next === 'running' ? `已启动任务「${t.name}」，执行器将自动拨打 pending 号码` : `已暂停任务「${t.name}」`);
      await reload();
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // ── 号码清单展开区 ──
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [targets, setTargets] = useState<CallTargetDTO[]>([]);
  const [progress, setProgress] = useState<CallTargetProgress | null>(null);
  const [newPhone, setNewPhone] = useState('');
  // 结构化导入：粘贴文本 + 解析结果 + 绑定 prompt 占位符 + 单号码最大拨打次数
  const [importText, setImportText] = useState('');
  const [importPlaceholders, setImportPlaceholders] = useState<string[]>([]);
  const [importTaskBizType, setImportTaskBizType] = useState<string | undefined>(undefined);
  const [importMaxAttempts, setImportMaxAttempts] = useState(1);

  // 即时预览：文本 / 占位符 / 任务 biz_type（取自绑定 prompt）任一变化即重解析
  const importResult = useMemo(
    () => (importText.trim()
      ? parseImportCsv(importText, importPlaceholders, importTaskBizType)
      : null),
    [importText, importPlaceholders, importTaskBizType],
  );

  const loadTargets = useCallback(async (taskId: number) => {
    try {
      const [tg, pg] = await Promise.all([
        callTargetsApi.list(taskId),
        callTargetsApi.progress(taskId).catch(() => null),
      ]);
      setTargets(tg);
      setProgress(pg);
    } catch (e) {
      flash('err', (e as Error).message);
    }
  }, []);

  const toggleExpand = async (t: CallTaskDTO) => {
    if (expandedId === t.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(t.id);
    setNewPhone('');
    setImportText('');
    // 取绑定 prompt 占位符（systemPrompt {占位符} ∪ 显式 variables）+ biz_type 供导入预览比对
    const p = await promptsApi.get(t.promptId).catch(() => null);
    setImportPlaceholders(
      p ? [...new Set([...extractPlaceholders(p.systemPrompt), ...p.variables])] : [],
    );
    setImportTaskBizType(p?.bizType);
    await loadTargets(t.id);
  };

  const addPhone = async (taskId: number) => {
    const phone = newPhone.trim();
    if (!phone) return;
    setBusy(true);
    try {
      await callTargetsApi.create(taskId, phone);
      setNewPhone('');
      flash('ok', `已添加号码 ${phone}`);
      await loadTargets(taskId);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const downloadTemplate = () => {
    const blob = new Blob([IMPORT_TEMPLATE_CSV], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'call-targets-template.csv';
    a.click();
    URL.revokeObjectURL(url);
  };

  const onImportFile = async (file: File) => {
    try {
      setImportText(await file.text());
    } catch {
      flash('err', '读取文件失败');
    }
  };

  const submitImport = async (taskId: number) => {
    if (!importResult || importResult.validCount === 0) return;
    setBusy(true);
    try {
      const payload = importResult.rows
        .filter((r) => !r.error)
        .map((r) => ({ phone: r.phone, customerId: r.customerId, vars: r.vars }));
      const r = await callTargetsApi.importStructured(taskId, payload, importMaxAttempts);
      setImportText('');
      flash('ok', `已导入 ${r.inserted} 个号码${r.skipped ? `（跳过重复 ${r.skipped}）` : ''}`);
      await loadTargets(taskId);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const removeTarget = async (taskId: number, targetId: number) => {
    setBusy(true);
    try {
      await callTargetsApi.remove(taskId, targetId);
      await loadTargets(taskId);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // running 任务轮询进度（M4 简版：展开时手动刷新 + 启停后 reload）
  useEffect(() => {
    if (expandedId === null) return;
    const t = tasks.find((x) => x.id === expandedId);
    if (!t || t.status !== 'running') return;
    const iv = setInterval(() => loadTargets(expandedId), 5000);
    return () => clearInterval(iv);
  }, [expandedId, tasks, loadTargets]);

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <div>
          <h2 className="text-base font-bold text-slate-800">外呼任务</h2>
          <p className="text-xs text-slate-500 mt-1">
            绑定提示词（三元组）+ 策略参数，落 call_task 表。执行引擎（originate / 调度 / 重拨 / 并发）代码已就绪，尚未接入主进程启用，故运行时暂不发起外呼。
          </p>
        </div>
        <button
          onClick={openCreate}
          disabled={prompts.length === 0}
          title={prompts.length === 0 ? '先在「提示词」创建并发布至少一条提示词' : '新增任务'}
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
                placeholder="例：09:00-21:00（策略参数，执行启用后生效）"
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
            <label className="text-xs text-slate-500 font-semibold">状态（执行器未启用，仅存储不自动流转）</label>
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
                <Fragment key={t.id}>
                  <tr className="hover:bg-slate-50/50">
                    <td className="p-3 font-semibold text-slate-800">
                      <button onClick={() => toggleExpand(t)} className="inline-flex items-center gap-1 hover:text-indigo-600">
                        {expandedId === t.id ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                        {t.name}
                      </button>
                    </td>
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
                        <button onClick={() => toggleRun(t)} disabled={busy || t.status === 'completed' || t.status === 'idle' && false} title={t.status === 'running' ? '暂停' : '启动外呼'} className={`p-1.5 rounded disabled:opacity-40 ${t.status === 'running' ? 'text-amber-600 hover:bg-amber-50' : 'text-emerald-600 hover:bg-emerald-50'}`}>
                          {t.status === 'running' ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                        </button>
                        <button onClick={() => openEdit(t)} disabled={busy} title="编辑" className="p-1.5 text-indigo-600 hover:bg-indigo-50 rounded disabled:opacity-40"><Edit3 className="w-3.5 h-3.5" /></button>
                        <button onClick={() => doDelete(t)} disabled={busy} title="删除" className="p-1.5 text-rose-600 hover:bg-rose-50 rounded disabled:opacity-40"><Trash2 className="w-3.5 h-3.5" /></button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === t.id && (
                    <tr key={`${t.id}-exp`}>
                      <td colSpan={7} className="p-4 bg-slate-50/40 border-t border-slate-100">
                        <div className="space-y-3">
                          {/* 进度条 */}
                          {progress && (
                            <div className="flex flex-wrap gap-3 text-[11px]">
                              <span className="font-semibold text-slate-600">进度：</span>
                              <span>总数 {progress.total}</span>
                              <span className="text-slate-500">待呼 {progress.pending}</span>
                              <span className="text-blue-600">呼叫中 {progress.dialing}</span>
                              <span className="text-emerald-600">已接通 {progress.answered + progress.done}</span>
                              <span className="text-amber-600">未接 {progress.noAnswer}</span>
                              <span className="text-rose-600">失败 {progress.failed}</span>
                            </div>
                          )}
                          {/* 录入区 */}
                          <div className="flex flex-wrap items-end gap-2">
                            <div className="space-y-1">
                              <label className="text-[11px] text-slate-500 font-semibold">单条录入</label>
                              <input
                                value={newPhone}
                                onChange={(e) => setNewPhone(e.target.value)}
                                onKeyDown={(e) => e.key === 'Enter' && addPhone(t.id)}
                                placeholder="号码，如 1000"
                                className="text-xs p-2 bg-white border border-slate-200 rounded-lg w-40 focus:outline-none focus:border-indigo-600"
                              />
                            </div>
                            <button onClick={() => addPhone(t.id)} disabled={busy || !newPhone.trim()} className="px-3 py-2 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 disabled:opacity-40 font-semibold">添加</button>
                          </div>
                          {/* 结构化批量导入：固定 5 列模板，支持粘贴/上传 + 即时预览 */}
                          <div className="flex flex-col gap-2">
                            <div className="flex items-center justify-between">
                              <label className="text-[11px] text-slate-500 font-semibold flex items-center gap-1">
                                <Upload className="w-3 h-3" /> 批量导入（序号｜业务类型｜手机号｜客户id｜json）
                              </label>
                              <button onClick={downloadTemplate} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-700 font-semibold">
                                <FileDown className="w-3 h-3" /> 下载模板
                              </button>
                            </div>
                            <textarea
                              value={importText}
                              onChange={(e) => setImportText(e.target.value)}
                              placeholder={'序号,业务类型,手机号,客户id,json\n1,collection,138****5678,C10001,{"customer_name":"张三","amount":"1200"}'}
                              rows={4}
                              className="text-xs p-2 bg-white border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-indigo-600"
                            />
                            <div className="flex items-center gap-2">
                              <label className="flex items-center gap-1 text-[11px] text-slate-500 font-semibold cursor-pointer hover:text-slate-700">
                                <Upload className="w-3 h-3" /> 上传 .csv
                                <input
                                  type="file" accept=".csv,text/csv" className="hidden"
                                  onChange={(e) => { const f = e.target.files?.[0]; if (f) onImportFile(f); e.target.value = ''; }}
                                />
                              </label>
                            </div>

                            {importResult && (
                              <div className="bg-white border border-slate-200 rounded-lg p-2.5 space-y-2">
                                {/* 汇总条 */}
                                <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-semibold">
                                  <span className="text-slate-600">共 {importResult.totalRows} 行</span>
                                  <span className="text-emerald-600">有效 {importResult.validCount}</span>
                                  {importResult.errorCount > 0 && <span className="text-rose-600">错误 {importResult.errorCount}</span>}
                                  {importResult.warningCount > 0 && <span className="text-amber-600">biz_type 不一致 {importResult.warningCount}</span>}
                                  {!importResult.hasPhoneColumn && <span className="text-rose-500">缺少「手机号」列</span>}
                                </div>
                                {/* 占位符比对 */}
                                {importPlaceholders.length > 0 && (
                                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px]">
                                    <span className="text-slate-400">变量覆盖:</span>
                                    {importResult.placeholders.hit.map((v) => (
                                      <span key={`h-${v}`} className="px-1.5 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">{v}</span>
                                    ))}
                                    {importResult.placeholders.missing.map((v) => (
                                      <span key={`m-${v}`} className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200" title="无任何行提供此变量">{v}（缺）</span>
                                    ))}
                                    {importResult.placeholders.extra.map((v) => (
                                      <span key={`e-${v}`} className="px-1.5 py-0.5 rounded bg-slate-50 text-slate-400 border border-slate-200" title="prompt 无此占位符，多余">{v}</span>
                                    ))}
                                  </div>
                                )}
                                {/* 前 5 行预览 */}
                                {importResult.rows.length > 0 && (
                                  <div className="max-h-40 overflow-y-auto border-t border-slate-100">
                                    <table className="w-full text-[10px]">
                                      <thead className="bg-slate-50 text-slate-500 sticky top-0">
                                        <tr>
                                          <th className="text-left p-1.5 font-semibold">序号</th>
                                          <th className="text-left p-1.5 font-semibold">手机号</th>
                                          <th className="text-left p-1.5 font-semibold">客户id</th>
                                          <th className="text-left p-1.5 font-semibold">json/错误</th>
                                        </tr>
                                      </thead>
                                      <tbody className="divide-y divide-slate-50">
                                        {importResult.rows.slice(0, 5).map((r, i) => (
                                          <tr key={i} className={r.error ? 'bg-rose-50/40' : r.warning ? 'bg-amber-50/40' : ''}>
                                            <td className="p-1.5 text-slate-400">{r.seq ?? i + 1}</td>
                                            <td className="p-1.5 font-mono text-slate-700">{r.phone || '—'}</td>
                                            <td className="p-1.5 font-mono text-slate-500">{r.customerId ?? '—'}</td>
                                            <td className="p-1.5">
                                              {r.error ? (
                                                <span className="text-rose-600">{r.error}</span>
                                              ) : (
                                                <span className="font-mono text-slate-500 truncate block max-w-[220px]" title={JSON.stringify(r.vars)}>
                                                  {Object.keys(r.vars).length ? JSON.stringify(r.vars) : '—'}
                                                </span>
                                              )}
                                              {!r.error && r.warning && <span className="block text-amber-600">{r.warning}</span>}
                                            </td>
                                          </tr>
                                        ))}
                                      </tbody>
                                    </table>
                                  </div>
                                )}
                              </div>
                            )}

                            <div className="flex items-center gap-2">
                              <label className="flex items-center gap-1 text-[11px] text-slate-500 font-semibold">
                                最大拨打
                                <input
                                  type="number" min={1} max={10} value={importMaxAttempts}
                                  onChange={(e) => setImportMaxAttempts(Math.max(1, Number(e.target.value) || 1))}
                                  className="w-14 text-xs p-1 bg-white border border-slate-200 rounded-lg text-center focus:outline-none focus:border-indigo-600"
                                />
                              </label>
                              <button
                                onClick={() => submitImport(t.id)}
                                disabled={busy || !importResult || importResult.validCount === 0}
                                className="px-3 py-2 bg-slate-700 text-white text-xs rounded-lg hover:bg-slate-800 disabled:opacity-40 font-semibold"
                              >
                                导入有效 {importResult?.validCount ?? 0} 行
                              </button>
                            </div>
                          </div>
                          {/* 号码列表 */}
                          <div className="bg-white rounded-lg border border-slate-100 overflow-hidden">
                            <div className="px-3 py-2 border-b border-slate-100 text-[11px] font-bold text-slate-600">号码清单 ({targets.length})</div>
                            {targets.length === 0 ? (
                              <p className="text-slate-400 text-xs text-center py-6">暂无号码，录入或批量导入</p>
                            ) : (
                              <div className="max-h-60 overflow-y-auto">
                                <table className="w-full text-xs">
                                  <thead className="bg-slate-50 text-slate-500 sticky top-0">
                                    <tr>
                                      <th className="text-left p-2 font-semibold">号码</th>
                                      <th className="text-left p-2 font-semibold">客户id</th>
                                      <th className="text-left p-2 font-semibold">状态</th>
                                      <th className="text-left p-2 font-semibold">已拨</th>
                                      <th className="text-left p-2 font-semibold">上次结果</th>
                                      <th className="text-right p-2 font-semibold">操作</th>
                                    </tr>
                                  </thead>
                                  <tbody className="divide-y divide-slate-50">
                                    {targets.map((tg) => (
                                      <tr key={tg.id}>
                                        <td className="p-2 font-mono text-slate-700">{tg.phoneMasked ?? tg.userKey}</td>
                                        <td className="p-2 font-mono text-slate-500 text-[10px]">{tg.customerId ?? '—'}</td>
                                        <td className="p-2">
                                          <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${
                                            tg.status === 'answered' || tg.status === 'done' ? 'bg-emerald-100 text-emerald-700' :
                                            tg.status === 'dialing' ? 'bg-blue-100 text-blue-700' :
                                            tg.status === 'failed' ? 'bg-rose-100 text-rose-700' :
                                            tg.status === 'no_answer' ? 'bg-amber-100 text-amber-700' :
                                            'bg-slate-100 text-slate-500'
                                          }`}>{tg.status}</span>
                                        </td>
                                        <td className="p-2 text-slate-600">{tg.attemptCount}/{tg.maxAttempts}</td>
                                        <td className="p-2 text-slate-500 font-mono text-[10px]">{tg.lastHangupCause ?? '—'}</td>
                                        <td className="p-2 text-right">
                                          <button onClick={() => removeTarget(t.id, tg.id)} disabled={busy} className="p-1 text-rose-600 hover:bg-rose-50 rounded disabled:opacity-40"><Trash2 className="w-3 h-3" /></button>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
