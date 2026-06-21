import { redirect } from 'next/navigation';
import { getSession } from '@/auth/session';
import ConsoleShell from '@/components/ConsoleShell';
import PromptManager from '@/components/PromptManager';

export default async function PromptsPage() {
  const session = await getSession();
  if (!session) redirect('/login');

  const user = session.user as { email: string; name: string; tenantId?: string };

  return (
    <ConsoleShell tenantId={user.tenantId ?? 'default'} userEmail={user.email} userName={user.name}>
      <PromptManager />
    </ConsoleShell>
  );
}
