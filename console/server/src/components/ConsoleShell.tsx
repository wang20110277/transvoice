'use client';

import { useState } from 'react';
import Link from 'next/link';
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
  Building2,
  LogOut,
} from 'lucide-react';
import { authClient } from '@/lib/auth-client';
import TenantSwitcher from './TenantSwitcher';

interface MenuItem {
  key: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  href?: string;
  enabled: boolean;
}

// 侧栏:纯业务菜单。平台管理功能(租户管理)放顶栏,按权限显隐。
const MENUS: MenuItem[] = [
  { key: 'home', label: '首页', icon: LayoutDashboard, href: '/', enabled: true },
  { key: 'routes', label: '呼入路由', icon: Route, href: '/inbound-routes', enabled: true },
  { key: 'prompts', label: '提示词管理', icon: Layers, href: '/prompts', enabled: true },
  { key: 'callcenter', label: '外呼任务', icon: PhoneCall, href: '/call-tasks', enabled: true },
  { key: 'records', label: '通话记录', icon: FileSpreadsheet, href: '/calls', enabled: true },
  { key: 'kb', label: '知识库', icon: Database, enabled: false },
  { key: 'memory', label: '记忆系统', icon: BrainCircuit, enabled: false },
  { key: 'rbac', label: '权限管理', icon: Shield, enabled: false },
];

export default function ConsoleShell({
  tenantId,
  userEmail,
  userName,
  role,
  children,
}: {
  tenantId: string;
  userEmail: string;
  userName: string;
  role: string;
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
        <div className="h-14 px-4 border-b border-slate-100 flex items-center">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-indigo-600 flex items-center justify-center text-white font-bold text-sm">智</div>
            {!collapsed && <span className="text-sm font-bold text-slate-800">外呼控制台</span>}
          </div>
        </div>

        <nav className="flex-1 p-2 overflow-y-auto">
          <div className="space-y-0.5">
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
          </div>
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
        <header className="h-14 bg-white border-b border-slate-100 px-6 flex justify-between items-center">
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">租户</span>
            <TenantSwitcher currentTenantId={tenantId} role={role} />
            {/* 租户管理入口:放顶栏切换器区,仅 platform_admin 可见 */}
            {role === 'platform_admin' && (
              <Link
                href="/tenants"
                title="租户管理"
                className={`flex items-center gap-1 text-xs px-2 py-1 rounded transition-colors ${
                  pathname === '/tenants'
                    ? 'bg-indigo-50 text-indigo-700 font-bold'
                    : 'text-slate-600 hover:bg-slate-100'
                }`}
              >
                <Building2 className="w-3.5 h-3.5" />
                租户管理
              </Link>
            )}
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
