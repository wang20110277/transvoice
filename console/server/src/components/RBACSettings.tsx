import React, { useState } from 'react';
import { User, Role, Permission } from '../types';
import { ALL_PERMISSIONS } from '../data/mockDb';
import { ShieldCheck, UserPlus, Fingerprint, Lock, ShieldAlert, Key, CheckSquare, Square, Info } from 'lucide-react';

interface RBACSettingsProps {
  users: User[];
  roles: Role[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onAddRole: (role: Role) => void;
  onUpdateRole: (role: Role) => void;
  onAddUser: (user: User) => void;
  onUpdateUser: (user: User) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

export default function RBACSettings({
  users,
  roles,
  activeTenantId,
  hasPermission,
  onAddRole,
  onUpdateRole,
  onAddUser,
  onUpdateUser,
  onAddAuditLog,
}: RBACSettingsProps) {
  const tenantUsers = users.filter(u => u.tenantId === activeTenantId);
  const tenantRoles = roles.filter(r => r.tenantId === activeTenantId);

  // States
  const [selectedRoleForMatrix, setSelectedRoleForMatrix] = useState<Role | null>(tenantRoles[0] || null);

  // Add User Form States
  const [isAddingUser, setIsAddingUser] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newRealName, setNewRealName] = useState('');
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserRoleId, setNewUserRoleId] = useState(tenantRoles[0]?.id || '');

  // Add Role Form States
  const [isAddingRole, setIsAddingRole] = useState(false);
  const [newRoleName, setNewRoleName] = useState('');
  const [newRoleDesc, setNewRoleDesc] = useState('');

  // Save new Role creation (RBAC checked)
  const handleCreateRole = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRoleName.trim() || !hasPermission('role:create')) return;

    const newRole: Role = {
      id: `role-${Date.now()}`,
      tenantId: activeTenantId,
      name: newRoleName,
      description: newRoleDesc || '自定义业务权限组合。',
      permissions: ['menu:prompt', 'prompt:view'], // Preset basic view permission
      isSystem: false
    };

    onAddRole(newRole);
    setSelectedRoleForMatrix(newRole);
    onAddAuditLog('安全管理', '增设角色', `创建了全新商业角色 [${newRoleName}]`);
    setNewRoleName('');
    setNewRoleDesc('');
    setIsAddingRole(false);
  };

  // Save new Employee Creation
  const handleCreateUser = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUsername.trim() || !newUserRoleId || !hasPermission('user:create')) return;

    const newUser: User = {
      id: `user-${Date.now()}`,
      tenantId: activeTenantId,
      username: newUsername,
      realName: newRealName || newUsername,
      email: newUserEmail || 'staff@tenant.com',
      roleId: newUserRoleId,
      status: 'active',
      createdAt: new Date().toISOString().split('T')[0]
    };

    onAddUser(newUser);
    onAddAuditLog('员工控制', '邀请企业伙伴', `向本部门引入了全新雇工 [${newUser.realName}]，赋予角色 [${roles.find(r => r.id === newUserRoleId)?.name}]`);
    setNewUsername('');
    setNewRealName('');
    setNewUserEmail('');
    setIsAddingUser(false);
  };

  // Mutate checkbox permission inside Role
  const handleTogglePermission = (role: Role, permCode: string) => {
    if (!hasPermission('role:create')) return; // Check if user has granular role update scope
    
    // Create new array copy
    let updatedPerms = [...role.permissions];
    if (updatedPerms.includes(permCode)) {
      updatedPerms = updatedPerms.filter(code => code !== permCode);
    } else {
      updatedPerms.push(permCode);
    }

    const updatedRole: Role = {
      ...role,
      permissions: updatedPerms
    };

    onUpdateRole(updatedRole);
    setSelectedRoleForMatrix(updatedRole);
    onAddAuditLog('安全管理', '修改角色矩阵', `调整了角色 《${role.name}》 的授权颗粒。变更权限点: [${permCode}]`);
  };

  // Bulk set all permissions for a role
  const handleSetAllPermissions = (role: Role, enableAll: boolean) => {
    if (!hasPermission('role:create')) return;
    const updatedRole: Role = {
      ...role,
      permissions: enableAll ? ALL_PERMISSIONS.map(p => p.code) : ['menu:prompt', 'prompt:view']
    };
    onUpdateRole(updatedRole);
    setSelectedRoleForMatrix(updatedRole);
    onAddAuditLog('安全管理', '重置全部权限', `对角色 《${role.name}》 进行了 ${enableAll ? '全开' : '精简置空'} 强设。`);
  };

  // Group permissions dynamically by category
  const groupedPermissions: { [key: string]: Permission[] } = {};
  ALL_PERMISSIONS.forEach(p => {
    if (!groupedPermissions[p.category]) groupedPermissions[p.category] = [];
    groupedPermissions[p.category].push(p);
  });

  return (
    <div className="space-y-6">
      
      {/* Top section with overview */}
      <div className="bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-800 flex items-center gap-1.5">
            <Lock className="w-5 h-5 text-indigo-600" />
            独立企业自治 RBAC 权限管理网 (Access Matrix)
          </h2>
          <p className="text-xs text-slate-500 mt-1">每个企业租户开通后配置独立的雇员账户体系和独立的RBAC逻辑。支持高度弹性的按钮/菜单双维度功能拦截控制。</p>
        </div>

        {/* Dynamic Warning of Simulation check */}
        <span className="text-[11px] font-sans text-amber-800 bg-amber-50 px-3 py-1.5 rounded-lg border border-amber-200 flex items-center gap-1.5 max-w-sm">
          <Fingerprint className="w-4 h-4 text-amber-600 animate-pulse shrink-0" />
          管理员可在此给角色划线，然后在顶部蓝色「模拟身份栏」立刻加载测试效果！
        </span>
      </div>

      {/* Main Grid: Left is Users Grid, Right is customizable permission matrix */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Subsection: User Registry List (Col span 5/12) */}
        <div className="lg:col-span-5 space-y-6">
          <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-xs space-y-4">
            <div className="flex justify-between items-center border-b border-slate-100 pb-2">
              <span className="text-xs font-bold text-slate-700">1. 本司登录用户名册 ({tenantUsers.length}名)</span>
              
              {!isAddingUser && (
                <button
                  type="button"
                  onClick={() => {
                    if (hasPermission('user:create')) setIsAddingUser(true);
                  }}
                  disabled={!hasPermission('user:create')}
                  className={`flex items-center gap-1 px-3 py-1.5 text-[10px] font-semibold rounded cursor-pointer ${
                    hasPermission('user:create') ? 'bg-indigo-600 text-white hover:bg-indigo-700' : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                  }`}
                >
                  <UserPlus className="w-3 h-3" /> 新设子账户
                </button>
              )}
            </div>

            {/* Display creating form overlay inside frame */}
            {isAddingUser && (
              <form onSubmit={handleCreateUser} className="p-3 bg-slate-50 rounded-lg border border-slate-200 text-xs space-y-3">
                <div className="flex justify-between items-center font-bold">
                  <span>添加并授权员工</span>
                  <button type="button" onClick={() => setIsAddingUser(false)} className="text-[10px] text-slate-400">关闭</button>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    placeholder="登录工号账号 (拼音)"
                    value={newUsername}
                    onChange={e => setNewUsername(e.target.value)}
                    required
                    className="p-1.5 bg-white border rounded"
                  />
                  <input
                    type="text"
                    placeholder="中文真实姓名"
                    value={newRealName}
                    onChange={e => setNewRealName(e.target.value)}
                    className="p-1.5 bg-white border rounded"
                  />
                </div>

                <div className="grid grid-cols-1 gap-2">
                  <input
                    type="email"
                    placeholder="绑定工作邮箱"
                    value={newUserEmail}
                    onChange={e => setNewUserEmail(e.target.value)}
                    className="p-1.5 bg-white border rounded"
                  />
                  
                  {/* select roles bindings */}
                  <div className="space-y-1">
                    <label className="text-[10px] text-slate-400 block font-bold">设定分配职责角色</label>
                    <select
                      value={newUserRoleId}
                      onChange={e => setNewUserRoleId(e.target.value)}
                      required
                      className="w-full p-2 bg-white border rounded"
                    >
                      {tenantRoles.map(r => (
                        <option key={r.id} value={r.id}>{r.name}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="flex justify-end gap-1.5">
                  <button type="button" onClick={() => setIsAddingUser(false)} className="bg-slate-200 px-3 py-1 rounded text-[11px] font-bold">取消</button>
                  <button type="submit" className="bg-indigo-600 text-white px-3 py-1 rounded text-[11px] font-bold">确认开户并划片</button>
                </div>
              </form>
            )}

            {/* Users grid map */}
            <div className="space-y-2.5 max-h-[380px] overflow-y-auto pr-1">
              {tenantUsers.map(u => {
                const role = roles.find(r => r.id === u.roleId);
                return (
                  <div key={u.id} className="p-3 bg-slate-50 border border-slate-150 rounded-lg text-left flex justify-between items-center text-xs">
                    <div className="space-y-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-bold text-slate-800 text-[13px]">{u.realName}</span>
                        <span className="text-[10px] bg-indigo-100 text-indigo-700 font-semibold px-2 border border-indigo-200/50 rounded-sm">
                          {role ? role.name : '未关联角色'}
                        </span>
                      </div>
                      <p className="text-[10px] text-slate-400 font-mono">工号：{u.username} • 邮箱: {u.email}</p>
                    </div>

                    <div className="flex flex-col items-end gap-1 font-sans shrink-0 border-l border-slate-200 pl-3">
                      <span className="text-[10px] text-emerald-700 font-semibold">● 存活运行中</span>
                      
                      {/* Allow dynamic role transfer */}
                      <select
                        value={u.roleId}
                        onChange={e => {
                          if (hasPermission('user:create')) {
                            const updatedUser: User = { ...u, roleId: e.target.value };
                            onUpdateUser(updatedUser);
                            onAddAuditLog('安全管理', '岗位岗位穿梭分流', `重新把员工 [${u.realName}] 的系统角色变迁为 [${roles.find(r => r.id === e.target.value)?.name}]`);
                            alert(`岗分配变更！员工已成功迁移。`);
                          }
                        }}
                        disabled={!hasPermission('user:create')}
                        className="text-[10px] bg-white border rounded py-0.5 px-1 font-bold text-slate-600 cursor-pointer disabled:opacity-45"
                      >
                        {tenantRoles.map(r => (
                          <option key={r.id} value={r.id}>{r.name}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Subsection: Customizable Permission Matrix Table (Col span 7/12) */}
        <div className="lg:col-span-7 bg-white rounded-xl border border-slate-100 p-5 shadow-xs flex flex-col justify-between h-[600px] overflow-hidden text-left">
          <div className="space-y-4 flex-1 flex flex-col overflow-hidden">
            
            {/* Header Select Role Matrix */}
            <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-2 border-b border-slate-150 pb-3 shrink-0">
              <div className="space-y-1">
                <span className="text-xs font-bold text-slate-800">2. 角色功能点矩阵分配细明表 (RBAC Master Map)</span>
                <p className="text-[10px] text-slate-400">选择待勾选的角色，并对按钮级操作行授权</p>
              </div>

              {/* Add Custom Roles Button */}
              {!isAddingRole ? (
                <button
                  type="button"
                  onClick={() => {
                    if (hasPermission('role:create')) setIsAddingRole(true);
                  }}
                  disabled={!hasPermission('role:create')}
                  className={`text-[9px] font-bold px-2.5 py-1.5 border rounded cursor-pointer ${
                    hasPermission('role:create') ? 'bg-slate-900 text-white hover:bg-black border-slate-800' : 'bg-slate-50 text-slate-400 border-slate-250 cursor-not-allowed'
                  }`}
                >
                  + 新设企业岗位
                </button>
              ) : (
                <form onSubmit={handleCreateRole} className="flex gap-1.5 text-xs">
                  <input
                    type="text"
                    placeholder="业务岗位命：质检专家B线"
                    value={newRoleName}
                    onChange={e => setNewRoleName(e.target.value)}
                    required
                    className="p-1 border bg-slate-50 text-[11px] rounded"
                  />
                  <button type="submit" className="bg-emerald-600 text-white text-[10px] px-2 rounded">保存</button>
                  <button type="button" onClick={() => setIsAddingRole(false)} className="bg-slate-150 text-slate-600 text-[10px] px-1.5 rounded">取消</button>
                </form>
              )}
            </div>

            {/* Select matrix dropdown menu */}
            <div className="flex gap-2 items-center shrink-0">
              <span className="text-xs font-semibold text-slate-500 font-sans">当前配置岗位:</span>
              <select
                value={selectedRoleForMatrix?.id || ''}
                onChange={e => {
                  const role = tenantRoles.find(r => r.id === e.target.value);
                  if (role) setSelectedRoleForMatrix(role);
                }}
                className="text-xs font-bold p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-indigo-500 focus:border-indigo-500"
              >
                {tenantRoles.map(r => (
                  <option key={r.id} value={r.id}>{r.name}</option>
                ))}
              </select>

              {/* Reset shortcut helper links (RBAC user) */}
              {selectedRoleForMatrix && (
                <div className="flex gap-1.5 ml-auto text-[10px]">
                  <button
                    onClick={() => handleSetAllPermissions(selectedRoleForMatrix, true)}
                    disabled={!hasPermission('role:create')}
                    className="bg-indigo-50 border border-indigo-200 text-indigo-700 px-2.5 py-1 rounded hover:bg-indigo-100 disabled:opacity-40"
                  >
                    全部满权勾通
                  </button>
                  <button
                    onClick={() => handleSetAllPermissions(selectedRoleForMatrix, false)}
                    disabled={!hasPermission('role:create')}
                    className="bg-slate-100 border border-slate-350 text-slate-700 px-2.5 py-1 rounded hover:bg-slate-150 disabled:opacity-40"
                  >
                    全部禁用精简
                  </button>
                </div>
              )}
            </div>

            {/* Scrollable checklists grids classified by Categories */}
            {selectedRoleForMatrix ? (
              <div className="flex-1 overflow-y-auto space-y-4.5 pr-2.5">
                
                {/* Description bar */}
                <p className="text-[11px] text-slate-500 bg-slate-50 p-3 rounded border border-slate-200/60 font-sans italic">
                  业务大纲：{selectedRoleForMatrix.description}
                </p>

                {Object.entries(groupedPermissions).map(([category, list]) => (
                  <div key={category} className="space-y-2 border border-slate-100 bg-slate-50/20 p-3.5 rounded-xl border border-slate-150">
                    <span className="text-[11px] font-bold text-slate-800 tracking-wide block border-b border-slate-100 pb-1 font-mono">{category} 功能集合</span>
                    
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
                      {list.map(perm => {
                        const checked = selectedRoleForMatrix.permissions.includes(perm.code);
                        return (
                          <label
                            key={perm.code}
                            className={`p-2 rounded-lg border flex items-start gap-2 cursor-pointer transition-all ${
                              checked 
                                ? 'border-indigo-150 bg-indigo-50/20 text-indigo-900' 
                                : 'border-slate-150 hover:bg-slate-100/50 text-slate-600'
                            } ${!hasPermission('role:create') ? 'opacity-70 cursor-not-allowed' : ''}`}
                          >
                            <input
                              type="checkbox"
                              checked={checked}
                              disabled={!hasPermission('role:create')}
                              onChange={() => handleTogglePermission(selectedRoleForMatrix, perm.code)}
                              className="mt-0.5 rounded text-indigo-600 focus:ring-0 cursor-pointer disabled:cursor-not-allowed shrink-0"
                            />
                            <div>
                              <span className="font-bold block tracking-tight text-[11px] font-sans">{perm.name}</span>
                              <span className="text-[9px] text-slate-400 leading-tight block mt-0.5 line-clamp-1">{perm.description}</span>
                            </div>
                          </label>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="py-20 text-center text-slate-400 text-xs">暂无选定角色</div>
            )}

          </div>
        </div>

      </div>
    </div>
  );
}
