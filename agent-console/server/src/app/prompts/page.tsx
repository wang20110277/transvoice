import { redirect } from 'next/navigation';
import { getSession, activeTenantIdOf } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import PromptManager from '@/components/PromptManager';

export default async function PromptsPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  const user = session.user as { email: string; name: string; tenantId?: string; role?: string };
  const tenantId = activeTenantIdOf(session) ?? user.tenantId ?? 'default';

  return (
    <ConsoleShell tenantId={tenantId} userEmail={user.email} userName={user.name} role={user.role ?? 'admin'}>
      <PromptManager />
    </ConsoleShell>
  );
}
