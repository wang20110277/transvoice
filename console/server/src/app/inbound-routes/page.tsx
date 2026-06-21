import { redirect } from 'next/navigation';
import { getSession } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import InboundRoutesManager from '@/components/InboundRoutesManager';

export default async function InboundRoutesPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  const user = session.user as { email: string; name: string; tenantId?: string };
  const tenantId = user.tenantId ?? 'default';

  return (
    <ConsoleShell tenantId={tenantId} userEmail={user.email} userName={user.name}>
      <InboundRoutesManager tenantId={tenantId} />
    </ConsoleShell>
  );
}
