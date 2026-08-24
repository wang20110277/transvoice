'use client';

import { useEffect, useState } from 'react';
import { RefreshCw, AlertCircle, PhoneCall, Clock, Hash } from 'lucide-react';
import HomeTrendChart from './HomeTrendChart';
import type { StatsResult } from '@/lib/stats-service';

function fmtDuration(ms: number): string {
  if (!ms) return '0s';
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60}s`;
}

/** 首页统计 — 概览卡片（今日/累计/平均时长）+ 近7天趋势 + biz_type 分布。 */
export default function HomePage({ tenantId }: { tenantId: string }) {
  const [stats, setStats] = useState<StatsResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch('/api/stats', { cache: 'no-store' });
      if (!r.ok) throw new Error(`加载失败 (${r.status})`);
      setStats(await r.json());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { reload(); }, []);

  const o = stats?.overview;
  const cards = [
    { label: '今日通话', value: String(o?.today ?? 0), icon: PhoneCall },
    { label: '累计通话', value: String(o?.total ?? 0), icon: Hash },
    { label: '平均时长', value: fmtDuration(o?.avgDurationMs ?? 0), icon: Clock },
  ];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-base font-bold text-slate-800">首页概览</h2>
          <p className="text-xs text-slate-500 mt-1">当前租户：<span className="font-mono">{tenantId}</span></p>
        </div>
        <button onClick={reload} className="text-slate-400 hover:text-slate-700">
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs p-2.5 rounded-lg border bg-rose-50 border-rose-200 text-rose-700">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {/* 概览卡片 */}
      <div className="grid grid-cols-3 gap-3">
        {cards.map((c) => {
          const Icon = c.icon;
          return (
            <div key={c.label} className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
              <div className="flex items-center justify-between">
                <span className="text-[11px] text-slate-500 font-semibold">{c.label}</span>
                <Icon className="w-4 h-4 text-slate-300" />
              </div>
              <p className="text-2xl font-bold text-slate-800 mt-2">{c.value}</p>
            </div>
          );
        })}
      </div>

      {/* 近 7 天趋势 */}
      {stats && <HomeTrendChart data={stats.trend} />}

      {/* biz_type 分布 */}
      {o && (
        <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
          <h3 className="text-sm font-bold text-slate-800 mb-3">业务类型分布</h3>
          <div className="space-y-2">
            {Object.entries(o.byBizType).map(([biz, n]) => {
              const pct = o.total > 0 ? (n / o.total) * 100 : 0;
              return (
                <div key={biz} className="flex items-center gap-3">
                  <span className="text-xs text-slate-600 w-32 font-mono">{biz}</span>
                  <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500 rounded-full" style={{ width: `${pct}%` }} />
                  </div>
                  <span className="text-xs text-slate-500 w-24 text-right">{n} 通（{pct.toFixed(0)}%）</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
