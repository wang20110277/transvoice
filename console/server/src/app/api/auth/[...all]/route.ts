/** Better Auth catch-all — 暴露 /api/auth/* (sign-in/sign-up/session 等)。 */
import { auth } from '@/auth';
import { toNextJsHandler } from 'better-auth/next-js';

export const { GET, POST } = toNextJsHandler(auth);
