'use client';

import type { StatsTrendPoint } from '@/lib/stats-service';

/**
 * 近 7 天通话量趋势 — 纯 CSS 柱状图（零依赖）。
 * 柱高 = count / maxCount * 100%；hover 显示日期 + 数量。
 */
export default function HomeTrendChart({ data }: { data: StatsTrendPoint[] }) {
  const max = Math.max(1, ...data.map((d) => d.count));
  return (
    <div className="bg-white p-4 rounded-xl border border-slate-100 shadow-xs">
      <h3 className="text-sm font-bold text-slate-800 mb-3">近 7 天通话量</h3>
      <div className="flex items-end justify-between gap-2 h-40">
        {data.map((d) => {
          // count=0 给极小高度（可见细线），否则按比例（至少 4%）
          const h = d.count === 0 ? 1.5 : Math.max(4, (d.count / max) * 100);
          const label = d.date.slice(5); // MM-DD
          return (
            <div key={d.date} className="flex-1 flex flex-col items-center gap-1 group h-full justify-end">
              <span className="text-[10px] font-semibold text-indigo-600 opacity-0 group-hover:opacity-100 transition-opacity">
                {d.count}
              </span>
              <div
                className="w-full max-w-[40px] bg-indigo-500 rounded-t hover:bg-indigo-600 transition-colors"
                style={{ height: `${h}%` }}
                title={`${d.date}：${d.count} 通`}
              />
              <span className="text-[10px] text-slate-400">{label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
