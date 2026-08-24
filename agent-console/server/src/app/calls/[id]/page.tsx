import { redirect } from 'next/navigation';
import { getSession, activeTenantIdOf } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import CallDetail from '@/components/CallDetail';

export default async function CallDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await getSession();
  if (!session) redirect('/login');
  const { id } = await params;
  const user = session.user as { email: string; name: string; tenantId?: string; role?: string };
  const tenantId = activeTenantIdOf(session) ?? user.tenantId ?? 'default';
  return (
    <ConsoleShell tenantId={tenantId} userEmail={user.email} userName={user.name} role={user.role ?? 'admin'}>
      <CallDetail id={Number(id)} />
    </ConsoleShell>
  );
}
