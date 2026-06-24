/**
 * RBAC — 角色 → 权限码。服务端守卫(prompt:create/update/delete/test)。
 * 与前端 mockDb 的权限码体系一致;本期 role 写在 console.user.role。
 */

export type PermissionCode =
  | 'menu:prompt'
  | 'menu:route'
  | 'menu:tenant'
  | 'prompt:view'
  | 'prompt:create'
  | 'prompt:update'
  | 'prompt:delete'
  | 'prompt:test'
  | 'route:view'
  | 'route:create'
  | 'route:update'
  | 'route:delete'
  | 'calltask:view'
  | 'calltask:create'
  | 'calltask:update'
  | 'calltask:delete'
  | 'call:view'
  | 'tenant:view'
  | 'tenant:create'
  | 'tenant:update'
  | 'tenant:delete'
  | 'user:view'
  | 'user:assign-tenant';

const ROLE_PERMISSIONS: Record<string, PermissionCode[]> = {
  admin: [
    'menu:prompt', 'prompt:view', 'prompt:create', 'prompt:update', 'prompt:delete', 'prompt:test',
    'menu:route', 'route:view', 'route:create', 'route:update', 'route:delete',
    'calltask:view', 'calltask:create', 'calltask:update', 'calltask:delete',
    'call:view',
  ],
  editor: [
    'menu:prompt', 'prompt:view', 'prompt:create', 'prompt:update', 'prompt:test',
    'menu:route', 'route:view',
    'calltask:view', 'calltask:create', 'calltask:update',
    'call:view',
  ],
  viewer: ['menu:prompt', 'prompt:view', 'menu:route', 'route:view', 'calltask:view', 'call:view'],
};

export function hasPermission(role: string, code: PermissionCode): boolean {
  // platform_admin 跨租户管理,拥有全部权限(admin 超集),短路返回 true
  if (role === 'platform_admin') return true;
  return (ROLE_PERMISSIONS[role] ?? ROLE_PERMISSIONS.viewer).includes(code);
}
