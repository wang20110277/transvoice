'use client';

import { useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import {
  Layers,
  Route,
  PhoneCall,
  Database,
  BrainCircuit,
  FileSpreadsheet,
  Shield,
  LayoutDashboard,
  LogOut,
} from 'lucide-react';
import { authClient } from '@/lib/auth-client';

interface MenuItem {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href?: string;
  enabled: boolean;
}

const MENUS: MenuItem[] = [
  { key: 'routes', label: 'DID 路由', icon: Route, href: '/inbound-routes', enabled: true },
  { key: 'prompts', label: '提示词管理', icon: Layers, href: '/prompts', enabled: true },
  { key: 'dashboard', label: '数据看板', icon: LayoutDashboard, enabled: false },
  { key: 'callcenter', label: '外呼任务', icon: PhoneCall, enabled: false },
  { key: 'records', label: '通话记录', icon: FileSpreadsheet, enabled: false },
  { key: 'kb', label: '知识库', icon: Database, enabled: false },
  { key: 'memory', label: '记忆系统', icon: BrainCircuit, enabled: false },
  { key: 'rbac', label: '权限管理', icon: Shield, enabled: false },
];

export default function ConsoleShell({
  tenantId,
  userEmail,
  userName,
  children,
}: {
  tenantId: string;
  userEmail: string;
  userName: string;
  children: React.ReactNode;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);

  const logout = async () => {
    await authClient.signOut();
    router.push('/login');
    router.refresh();
  };

  return (
    <div className="min-h-screen flex bg-slate-50">
      {/* 侧边栏 */}
      <aside className={`${collapsed ? 'w-16' : 'w-56'} bg-white border-r border-slate-100 flex flex-col transition-all`}>
        <div className="p-4 border-b border-slate-100">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-sm">智</div>
            {!collapsed && <span className="text-sm font-bold text-slate-800">外呼控制台</span>}
          </div>
        </div>

        <nav className="flex-1 p-2 space-y-0.5">
          {MENUS.map((m) => {
            const Icon = m.icon;
            const active = pathname === m.href;
            return (
              <button
                key={m.key}
                onClick={() => m.enabled && m.href && router.push(m.href)}
                disabled={!m.enabled}
                title={m.label}
                className={`w-full flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition-colors ${
                  active
                    ? 'bg-indigo-50 text-indigo-700 font-bold'
                    : m.enabled
                      ? 'text-slate-600 hover:bg-slate-50'
                      : 'text-slate-300 cursor-not-allowed'
                }`}
              >
                <Icon className="w-4 h-4 shrink-0" />
                {!collapsed && (
                  <span className="flex-1 text-left">
                    {m.label}
                    {!m.enabled && <span className="ml-1 text-[9px] text-slate-300">下期</span>}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-3 text-[10px] text-slate-400 hover:text-slate-600 border-t border-slate-100"
        >
          {collapsed ? '▶' : '◀ 收起'}
        </button>
      </aside>

      {/* 主区 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <header className="bg-white border-b border-slate-100 px-6 py-3 flex justify-between items-center">
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">租户</span>
            <span className="text-xs font-mono font-semibold bg-slate-100 px-2 py-0.5 rounded text-slate-700">{tenantId}</span>
          </div>
          <div className="flex items-center gap-3">
            <div className="text-right">
              <p className="text-xs font-semibold text-slate-700">{userName}</p>
              <p className="text-[10px] text-slate-400">{userEmail}</p>
            </div>
            <button
              onClick={logout}
              title="退出登录"
              className="p-2 text-slate-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </header>

        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>
    </div>
  );
}
