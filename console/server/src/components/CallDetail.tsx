'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, PlayCircle, AlertCircle, CheckCircle2 } from 'lucide-react';
import { callsApi, HttpError, type CallDetailClient } from '@/lib/calls-api';

function fmtTs(ts: Date | string): string {
  const d = typeof ts === 'string' ? new Date(ts) : ts;
  return d.toLocaleString('zh-CN', { hour12: false });
}
function fmtDuration(ms: number | null): string {
  if (ms == null) return '—';
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
}

export default function CallDetail({ id }: { id: number }) {
  const router = useRouter();
  const [data, setData] = useState<CallDetailClient | null>(null);
  const [loading, setLoading] = useState(true);
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [recordingChecked, setRecordingChecked] = useState(false);
  const [toast, setToast] = useState<{ kind: 'ok' | 'err'; msg: string } | null>(null);
  const [archiving, setArchiving] = useState(false);

  const flash = (kind: 'ok' | 'err', msg: string) => {
    setToast({ kind, msg });
    setTimeout(() => setToast(null), 3000);
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const d = await callsApi.detail(id);
      setData(d);
      // 录音 URL（有 artifacts 才请求；无 recording 返回 404 → 显示未归档）
      const hasRecording = d.artifacts.some((a) => a.kind === 'recording');
      if (hasRecording) {
        try {
          const r = await callsApi.recordingUrl(id);
          setRecordingUrl(r.url);
        } catch {
          setRecordingUrl(null);
        }
      }
      setRecordingChecked(true);
    } catch (e) {
      flash('err', (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  const handleArchive = async () => {
    setArchiving(true);
    try {
      await callsApi.archiveRecording(id);
      flash('ok', '归档成功');
      await load();  // 刷新 → artifact 出现 → recordingUrl → 播放器
    } catch (e) {
      const status = e instanceof HttpError ? e.status : 0;
      if (status === 409) { flash('ok', '录音已归档'); await load(); }
      else if (status === 410) flash('err', '录音文件已被清理，无法补归档');
      else if (status === 502) flash('err', '归档服务暂不可用，请稍后重试');
      else if (status === 404) flash('err', '通话记录不存在');
      else flash('err', (e as Error).message || '归档失败');
    } finally {
      setArchiving(false);
    }
  };

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <p className="text-slate-400 text-xs text-center py-10">加载中…</p>;
  if (!data) return <p className="text-slate-400 text-xs text-center py-10">通话不存在或无权访问</p>;

  const { session, turns, events, artifacts } = data;
  const recording = artifacts.find((a) => a.kind === 'recording');

  return (
    <div className="space-y-4">
      {toast && (
        <div className={`flex items-center gap-2 text-xs p-2.5 rounded-lg border ${
          toast.kind === 'ok' ? 'bg-emerald-50 border-emerald-200 text-emerald-700' : 'bg-rose-50 border-rose-200 text-rose-700'
        }`}>
          {toast.kind === 'ok' ? <CheckCircle2 className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
          {toast.msg}
        </div>
      )}

      {/* 返回 + 通话概要 */}
      <button onClick={() => router.push('/calls')} className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800">
        <ArrowLeft className="w-3.5 h-3.5" /> 返回列表
      </button>
      <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
          <Info label="手机号" value={session.phoneMasked ?? '—'} mono />
          <Info label="业务类型" value={session.bizType} />
          <Info label="开始" value={fmtTs(session.startTs)} />
          <Info label="时长" value={fmtDuration(session.durationMs)} />
          <Info label="call_id" value={session.callId} mono />
          <Info label="租户" value={session.tenantId ?? '—'} />
          <Info label="挂断原因" value={session.hangupCause ?? '—'} />
          <Info label="录音提示已播" value={session.recordingNoticePlayed ? '是' : '否'} />
        </div>
      </div>

      {/* 录音播放器 */}
      <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <h3 className="text-sm font-bold text-slate-800 mb-2 flex items-center gap-1.5">
          <PlayCircle className="w-4 h-4 text-indigo-600" /> 整通录音
        </h3>
        {recordingUrl ? (
          <audio controls src={recordingUrl} className="w-full" />
        ) : recordingChecked ? (
          recording ? (
            <p className="text-xs text-slate-400">录音存在但生成播放链接失败（MinIO 可能未配置）</p>
          ) : (
            <div className="flex items-center gap-3">
              <p className="text-xs text-slate-400">录音未归档</p>
              <button
                onClick={handleArchive}
                disabled={archiving}
                className="px-3 py-1 text-xs font-semibold rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {archiving ? '归档中…' : '手动归档'}
              </button>
            </div>
          )
        ) : null}
        {recording && (
          <p className="text-[11px] text-slate-400 mt-1 font-mono">
            {recording.uri} · {recording.sizeBytes ? Math.round(recording.sizeBytes / 1024) : '?'}KB · {recording.contentType}
          </p>
        )}
      </div>

      {/* 逐轮对话回放 */}
      <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
        <h3 className="text-sm font-bold text-slate-800 mb-3">逐轮对话（{turns.length}）</h3>
        {turns.length === 0 ? (
          <p className="text-xs text-slate-400">无对话记录</p>
        ) : (
          <div className="space-y-2">
            {turns.map((t) => (
              <div key={t.id} className={`flex ${t.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[70%] rounded-lg px-3 py-2 text-xs ${
                  t.role === 'user'
                    ? 'bg-slate-100 text-slate-800'
                    : 'bg-indigo-50 text-slate-800 border border-indigo-100'
                }`}>
                  <div className="text-[10px] font-semibold mb-0.5 opacity-60">
                    {t.role === 'user' ? '用户' : 'AI'} · {fmtTs(t.ts)}
                  </div>
                  <div className="whitespace-pre-wrap">{t.text || '(空)'}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 事件时间线 */}
      {events.length > 0 && (
        <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
          <h3 className="text-sm font-bold text-slate-800 mb-3">事件时间线（{events.length}）</h3>
          <div className="space-y-1.5">
            {events.map((e) => (
              <div key={e.id} className="flex items-start gap-2 text-xs">
                <span className="text-slate-400 font-mono shrink-0">{fmtTs(e.ts)}</span>
                <span className="px-1.5 py-0.5 rounded bg-amber-50 text-amber-700 text-[10px] font-semibold shrink-0">
                  {e.eventType}
                </span>
                <span className="text-slate-500 font-mono text-[10px] break-all">
                  {JSON.stringify(e.payload)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <div className="text-[10px] text-slate-400 font-semibold">{label}</div>
      <div className={`text-slate-700 ${mono ? 'font-mono text-[11px]' : ''} truncate`} title={value}>{value}</div>
    </div>
  );
}
