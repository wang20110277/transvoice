/** Better Auth 浏览器客户端(登录/登出/session)。 */
import { createAuthClient } from 'better-auth/react';

export const authClient = createAuthClient();

export const { signIn, signOut, useSession } = authClient;
