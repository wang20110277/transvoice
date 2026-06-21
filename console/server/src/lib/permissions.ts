/**
 * RBAC — 角色 → 权限码。服务端守卫(prompt:create/update/delete/test)。
 * 与前端 mockDb 的权限码体系一致;本期 role 写在 console_auth.user.role。
 */

export type PermissionCode =
  | 'menu:prompt'
  | 'menu:route'
  | 'prompt:view'
  | 'prompt:create'
  | 'prompt:update'
  | 'prompt:delete'
  | 'prompt:test'
  | 'route:view'
  | 'route:create'
  | 'route:update'
  | 'route:delete';

const ROLE_PERMISSIONS: Record<string, PermissionCode[]> = {
  admin: [
    'menu:prompt', 'prompt:view', 'prompt:create', 'prompt:update', 'prompt:delete', 'prompt:test',
    'menu:route', 'route:view', 'route:create', 'route:update', 'route:delete',
  ],
  editor: [
    'menu:prompt', 'prompt:view', 'prompt:create', 'prompt:update', 'prompt:test',
    'menu:route', 'route:view',
  ],
  viewer: ['menu:prompt', 'prompt:view', 'menu:route', 'route:view'],
};

export function hasPermission(role: string, code: PermissionCode): boolean {
  return (ROLE_PERMISSIONS[role] ?? ROLE_PERMISSIONS.viewer).includes(code);
}
