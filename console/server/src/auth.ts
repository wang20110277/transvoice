/**
 * Better Auth — Console 管理端认证。
 *
 * 本期:credentialProvider(email/password)本地账密,开箱可用。
 * ADFS:走 env 开关 CONSOLE_ADFS_ENABLED 预留(企业内网接入时填 issuer/clientId/secret
 *   并开启 genericOAuth 插件,见文末注释),本期默认关闭。
 *
 * session.user.tenantId —— 多租户隔离键;create_user/update_user 取自认证用户 email。
 */
import { betterAuth } from 'better-auth';
import { drizzleAdapter } from 'better-auth/adapters/drizzle';
import { db } from '@/db/client';
import * as schema from '@/db/schema';

const ADFS_ENABLED = process.env.CONSOLE_ADFS_ENABLED === 'true';

export const auth = betterAuth({
  database: drizzleAdapter(db, {
    provider: 'pg',
    schema: {
      user: schema.user,
      session: schema.session,
      account: schema.account,
      verification: schema.verification,
    },
  }),
  secret: process.env.BETTER_AUTH_SECRET,
  baseURL: process.env.BETTER_AUTH_URL ?? 'http://localhost:3001',
  trustedOrigins: [process.env.BETTER_AUTH_URL ?? 'http://localhost:3001'],

  emailAndPassword: {
    enabled: true,
    autoSignIn: true,
    requireEmailVerification: false, // 本地开发不强制邮件验证
  },

  user: {
    additionalFields: {
      tenantId: { type: 'string', required: false, defaultValue: 'default', input: false },
      role: { type: 'string', required: false, defaultValue: 'admin', input: false },
    },
  },

  // ADFS(OIDC)预留:企业内网接入时启用。本期本地账密,关闭。
  // 启用方式:安装 @better-auth/generic-oauth,放开下方 socialProviders.genericOAuth,
  // 并在 .env 填 CONSOLE_ADFS_ISSUER/CLIENT_ID/CLIENT_SECRET + CONSOLE_ADFS_ENABLED=true。
  ...(ADFS_ENABLED
    ? {
        // socialProviders: {
        //   genericOAuth: { ...adfsConfig },
        // },
      }
    : {}),
});

export type Session = typeof auth.$Infer.Session;
