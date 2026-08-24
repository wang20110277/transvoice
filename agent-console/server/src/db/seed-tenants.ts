/**
 * 租户管理 seed(幂等):
 *   1. 从现有 user.tenant_id 去重 → 回填 tenant 主表(default/galaxy_fin/...)
 *   2. 为每个 user 建立 user_tenant 主归属(is_primary=true)
 *   3. seed 一个 platform_admin 账号(跨租户管理)
 *
 * 配套 0002_tenant_management.sql(建表)。可重复执行,ON CONFLICT DO NOTHING。
 */
import { auth } from '../auth';
import { db } from './client';
import { user, tenant, userTenant } from './schema';
import { eq } from 'drizzle-orm';

const PLATFORM_SEED = {
  email: 'platform@transvoice.local',
  password: 'platform123',
  name: '平台管理员',
  tenantId: 'default',
};

async function seedTenants() {
  // 1. 回填 tenant 主表:现有 user.tenant_id 去重值 → tenant 记录
  const users = await db.select({ id: user.id, tenantId: user.tenantId }).from(user);
  const tenantIds = [...new Set(users.map((u) => u.tenantId).filter(Boolean))] as string[];
  for (const tid of tenantIds) {
    await db
      .insert(tenant)
      .values({ id: tid, name: tid, status: 'active' })
      .onConflictDoNothing({ target: tenant.id });
    console.log(`✓ tenant ${tid}`);
  }

  // 2. 为每个 user 建立 user_tenant 主归属
  for (const u of users) {
    await db
      .insert(userTenant)
      .values({ userId: u.id, tenantId: u.tenantId, isPrimary: true })
      .onConflictDoNothing({ target: [userTenant.userId, userTenant.tenantId] });
  }
  console.log(`✓ ${users.length} 个用户主归属已建`);

  // 3. seed platform_admin 跨租户管理账号
  try {
    await auth.api.signUpEmail({
      body: {
        email: PLATFORM_SEED.email,
        password: PLATFORM_SEED.password,
        name: PLATFORM_SEED.name,
      },
    });
  } catch {
    // 已存在则跳过
  }
  const [plat] = await db
    .select({ id: user.id })
    .from(user)
    .where(eq(user.email, PLATFORM_SEED.email));
  if (plat) {
    await db.update(user).set({ role: 'platform_admin' }).where(eq(user.id, plat.id));
    await db
      .insert(userTenant)
      .values({ userId: plat.id, tenantId: PLATFORM_SEED.tenantId, isPrimary: true })
      .onConflictDoNothing({ target: [userTenant.userId, userTenant.tenantId] });
    console.log(`✓ platform_admin ${PLATFORM_SEED.email}`);
  }

  console.log('seed-tenants done');
}

seedTenants()
  .then(() => process.exit(0))
  .catch((e) => {
    console.error('seed-tenants failed:', e);
    process.exit(1);
  });
