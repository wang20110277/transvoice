import type { NextConfig } from 'next';

const config: NextConfig = {
  reactStrictMode: true,
  // Drizzle/node-postgres 与 better-auth 都是 Node 原生依赖,服务端运行无需打包配置。
  serverExternalPackages: ['pg', 'ioredis', 'better-auth'],
};

export default config;
