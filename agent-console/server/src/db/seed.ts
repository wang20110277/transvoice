/**
 * 本地开发 seed:2 个管理员账号,分属两个 tenant,便于联调多租户隔离。
 *
 *   admin@transvoice.local / admin123  → tenant_id=default(可见现有 3 条提示词)
 *   fin@transvoice.local   / admin123  → tenant_id=galaxy_fin(空,演示隔离)
 *
 * 密码哈希走 better-auth signUp(与登录链路一致);tenant_id 因 input:false,
 * 在创建后由本脚本(可信)直接修正。
 */
import { auth } from '../auth';
import { db } from './client';
import { user } from './schema';
import { eq } from 'drizzle-orm';

const SEEDS = [
  { email: 'admin@transvoice.local', password: 'admin123', name: '默认管理员', tenantId: 'default' },
  { email: 'fin@transvoice.local', password: 'admin123', name: '星河金融管理员', tenantId: 'galaxy_fin' },
];

async function seed() {
  for (const s of SEEDS) {
    try {
      await auth.api.signUpEmail({
        body: { email: s.email, password: s.password, name: s.name },
      });
    } catch {
      // 已存在则跳过创建,继续修正租户
    }
    await db
      .update(user)
      .set({ tenantId: s.tenantId, role: 'admin' })
      .where(eq(user.email, s.email));
    console.log(`✓ ${s.email} → tenant=${s.tenantId}`);
  }
  console.log('seed done');
}

seed()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error('seed failed:', e);
    process.exit(1);
  });
