import { redirect } from 'next/navigation';
import { getSession } from '@/auth/session';

export default async function Home() {
  const session = await getSession();
  redirect(session ? '/prompts' : '/login');
}
