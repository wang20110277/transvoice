'use client';

/** 顶栏租户切换器:下拉显示当前用户可切换租户,切换后 router.refresh。 */
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronsUpDown, Check } from 'lucide-react';

interface TenantOpt {
  id: string;
  name: string;
}

export default function TenantSwitcher({
  currentTenantId,
  role,
}: {
  currentTenantId: string;
  role: string;
}) {
  const router = useRouter();
  const [opts, setOpts] = useState<TenantOpt[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    fetch('/api/session/tenants')
      .then((r) => r.json())
      .then((d) => setOpts(d.tenants ?? []))
      .catch(() => {});
  }, [open]);

  const switchTo = async (tenantId: string) => {
    setLoading(true);
    const r = await fetch('/api/session/switch-tenant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tenantId }),
    });
    setLoading(false);
    setOpen(false);
    if (r.ok) {
      router.refresh();
    } else {
      const e = await r.json().catch(() => ({}));
      alert(e.error ?? '切换失败');
    }
  };

  const current = opts.find((o) => o.id === currentTenantId);

  return (
    <div className="relative">
      <button
        onClick={() => setOpen(!open)}
        disabled={loading}
        className="flex items-center gap-1.5 text-xs font-mono font-semibold bg-slate-100 px-2 py-0.5 rounded text-slate-700 hover:bg-slate-200 disabled:opacity-50"
      >
        {current?.name ?? currentTenantId}
        <ChevronsUpDown className="w-3 h-3" />
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1 bg-white border border-slate-200 rounded-lg shadow-lg z-50 min-w-[160px]">
          {opts.length === 0 && <div className="px-3 py-2 text-xs text-slate-400">加载中...</div>}
          {opts.map((o) => (
            <button
              key={o.id}
              onClick={() => switchTo(o.id)}
              className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50"
            >
              <span>{o.name}</span>
              {o.id === currentTenantId && <Check className="w-3 h-3 text-indigo-600" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
