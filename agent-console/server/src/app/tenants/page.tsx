import { redirect } from 'next/navigation';
import { getSession, activeTenantIdOf } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import TenantsManager from '@/components/TenantsManager';

export default async function TenantsPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  const user = session.user as { email: string; name: string; tenantId?: string; role?: string };
  // 页面层守卫:非 platform_admin 重定向(与 API 层 /api/tenants 403 双保险)
  if (user.role !== 'platform_admin') redirect('/prompts');

  const tenantId = activeTenantIdOf(session) ?? user.tenantId ?? 'default';

  return (
    <ConsoleShell tenantId={tenantId} userEmail={user.email} userName={user.name} role={user.role ?? 'admin'}>
      <TenantsManager />
    </ConsoleShell>
  );
}
