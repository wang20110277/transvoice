import { describe, it, expect } from 'vitest';
import { hasPermission } from '../../src/lib/permissions';

describe('hasPermission', () => {
  it('platform_admin 短路:对所有权限码返回 true(admin 超集)', () => {
    expect(hasPermission('platform_admin', 'tenant:create')).toBe(true);
    expect(hasPermission('platform_admin', 'tenant:delete')).toBe(true);
    expect(hasPermission('platform_admin', 'user:assign-tenant')).toBe(true);
    expect(hasPermission('platform_admin', 'menu:tenant')).toBe(true);
    expect(hasPermission('platform_admin', 'prompt:view')).toBe(true);
  });

  it('admin 有 admin 权限,无 tenant/user 权限', () => {
    expect(hasPermission('admin', 'prompt:create')).toBe(true);
    expect(hasPermission('admin', 'route:delete')).toBe(true);
    expect(hasPermission('admin', 'tenant:create')).toBe(false);
    expect(hasPermission('admin', 'menu:tenant')).toBe(false);
    expect(hasPermission('admin', 'user:view')).toBe(false);
  });

  it('viewer 只读', () => {
    expect(hasPermission('viewer', 'prompt:view')).toBe(true);
    expect(hasPermission('viewer', 'prompt:create')).toBe(false);
  });

  it('未知角色 fallback viewer', () => {
    expect(hasPermission('unknown', 'prompt:view')).toBe(true);
    expect(hasPermission('unknown', 'prompt:create')).toBe(false);
  });
});
