'use client';

import { useCallback, useEffect, useState } from 'react';
import { RefreshCw, CheckCircle2, AlertCircle, FileSpreadsheet, Search } from 'lucide-react';
import { callsApi, type CallDetailClient } from '@/lib/calls-api';
import type { SessionDTO } from '@/lib/calls-service';
import { useRouter } from 'next/navigation';

const BIZ_TYPES = ['customer_service', 'collection', 'marketing'];

function fmtTs(d: Date | string): string {
  const dt = typeof d === 'string' ? new Date(d) : d;
  return dt.toLocaleString('zh-CN', { hour12: false });
}

function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

export default function CallRecordsList({ tenantId }: { tenantId: string }) {
  const router = useRouter();
  const [calls, setCalls] = useState<SessionDTO[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  // 筛选
  const [fBiz, setFBiz] = useState('');
  const [fPhone, setFPhone] = useState('');
  const [fFrom, setFFrom] = useState('');
  const [fTo, setFTo] = useState('');

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await callsApi.list({
        bizType: fBiz || undefined,
        phoneMasked: fPhone || undefined,
        startFrom: fFrom || undefined,
        startTo: fTo || undefined,
        page,
        pageSize,
      });
      setCalls(r.calls);
      setTotal(r.total);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [fBiz, fPhone, fFrom, fTo, page]);

  useEffect(() => {
    reload();
  }, [reload]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // 页码数组（含省略号）：首页 … 当前页±1 … 末页
  const pageNumbers: (number | '...')[] = (() => {
    const pages: (number | '...')[] = [];
    const rangeStart = Math.max(1, page - 1);
    const rangeEnd = Math.min(totalPages, page + 1);
    if (rangeStart > 1) {
      pages.push(1);
      if (rangeStart > 2) pages.push('...');
    }
    for (let i = rangeStart; i <= rangeEnd; i++) pages.push(i);
    if (rangeEnd < totalPages) {
      if (rangeEnd < totalPages - 1) pages.push('...');
      pages.push(totalPages);
    }
    return pages;
  })();

  return (
    <div className="space-y-4">
      <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <h2 className="text-base font-bold text-slate-800">通话记录</h2>
        <p className="text-xs text-slate-500 mt-1">
          按 call_id / 手机号 / 业务类型 / 时间区间 查看通话；点击行进入详情（逐轮对话 + 录音回放）。当前租户：
          <span className="font-mono">{tenantId}</span>
        </p>
      </div>

      {toast && (
        <div className={`flex items-center gap-2 text-xs p-2.5 rounded-lg border ${
          toast.kind === 'ok' ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-rose-50 border-rose-200 text-rose-700'
        }`}>
          {toast.kind === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      {/* 筛选区 */}
      <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="space-y-1">
            <label className="text-[11px] text-slate-500 font-semibold">业务类型</label>
            <select value={fBiz} onChange={(e) => { setFBiz(e.target.value); setPage(1); }}
              className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600">
              <option value="">全部</option>
              {BIZ_TYPES.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </div>
          <div className="space-y-1">
            <label className="text-[11px] text-slate-500 font-semibold">手机号（掩码模糊）</label>
            <input value={fPhone} onChange={(e) => { setFPhone(e.target.value); setPage(1); }}
              placeholder="138 / 5678" className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 font-mono" />
          </div>
          <div className="space-y-1">
            <label className="text-[11px] text-slate-500 font-semibold">开始时间 ≥</label>
            <input type="datetime-local" value={fFrom} onChange={(e) => { setFFrom(e.target.value); setPage(1); }}
              className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600" />
          </div>
          <div className="space-y-1">
            <label className="text-[11px] text-slate-500 font-semibold">开始时间 ≤</label>
            <input type="datetime-local" value={fTo} onChange={(e) => { setFTo(e.target.value); setPage(1); }}
              className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600" />
          </div>
        </div>
        <div className="flex justify-end gap-2 mt-3">
          <button onClick={() => { setFBiz(''); setFPhone(''); setFFrom(''); setFTo(''); setPage(1); }}
            className="px-3 py-1.5 bg-slate-100 text-slate-600 text-xs rounded-lg hover:bg-slate-200 font-semibold">清空</button>
          <button onClick={reload}
            className="flex items-center gap-1 px-3 py-1.5 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 font-semibold">
            <Search className="w-3.5 h-3.5" /> 查询
          </button>
        </div>
      </div>

      {/* 列表 */}
      <div className="bg-white rounded-xl border border-slate-100 shadow-xs overflow-hidden">
        <div className="flex justify-between items-center px-4 py-3 border-b border-slate-100">
          <span className="text-xs font-bold text-slate-700">通话列表（{total}）</span>
          <button onClick={reload} className="text-slate-400 hover:text-slate-700">
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
        {loading ? (
          <p className="text-slate-400 text-xs text-center py-10">加载中…</p>
        ) : calls.length === 0 ? (
          <div className="text-slate-400 text-xs text-center py-10 space-y-1">
            <FileSpreadsheet className="w-6 h-6 mx-auto opacity-40" />
            <p>暂无通话记录</p>
          </div>
        ) : (
          <table className="w-full text-xs">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="text-left p-3 font-semibold">call_id</th>
                <th className="text-left p-3 font-semibold">手机号</th>
                <th className="text-left p-3 font-semibold">业务类型</th>
                <th className="text-left p-3 font-semibold">开始时间</th>
                <th className="text-left p-3 font-semibold">时长</th>
                <th className="text-left p-3 font-semibold">挂断原因</th>
                <th className="text-left p-3 font-semibold">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {calls.map((c) => (
                <tr key={c.id} className="hover:bg-slate-50/60">
                  <td className="p-3 font-mono text-slate-500 truncate max-w-[180px]" title={c.callId}>
                    {c.callId.slice(0, 8)}…
                  </td>
                  <td className="p-3 font-mono text-slate-700">{c.phoneMasked ?? '—'}</td>
                  <td className="p-3 text-slate-600">{c.bizType}</td>
                  <td className="p-3 text-slate-600">{fmtTs(c.startTs)}</td>
                  <td className="p-3 text-slate-600">{fmtDuration(c.durationMs)}</td>
                  <td className="p-3 text-slate-500 truncate max-w-[150px]" title={c.hangupCause ?? ''}>{c.hangupCause ?? '—'}</td>
                  <td className="p-3">
                    <button
                      onClick={() => router.push(`/calls/${c.id}`)}
                      className="px-2.5 py-1 bg-indigo-50 text-indigo-600 text-[11px] rounded hover:bg-indigo-100 font-semibold"
                    >
                      查看详情
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* 分页：始终显示 + 页码跳转 */}
      <div className="flex justify-center items-center gap-1.5 text-xs text-slate-600">
        <button onClick={() => setPage(1)} disabled={page <= 1}
          className="px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50">首页</button>
        <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}
          className="px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50">上一页</button>
        {pageNumbers.map((p) =>
          typeof p === 'number' ? (
            <button key={p} onClick={() => setPage(p)}
              className={`min-w-[28px] px-2 py-1.5 border rounded-lg font-semibold ${
                p === page ? 'bg-indigo-600 text-white border-indigo-600' : 'bg-white border-slate-200 hover:bg-slate-50'
              }`}>{p}</button>
          ) : (
            <span key={p} className="px-1 text-slate-400">…</span>
          )
        )}
        <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages}
          className="px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg disabled:opacity-40 hover:bg-slate-50">下一页</button>
        <span className="ml-2 text-slate-400">共 {total} 条 / {totalPages} 页</span>
      </div>
    </div>
  );
}

// 供 CallDetail 复用的类型（避免循环依赖，从这里 re-export）
export type { CallDetailClient };
