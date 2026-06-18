import React, { useState } from 'react';
import {
  Tenant,
  User,
  Role,
  PromptTemplate,
  KnowledgeBase,
  CallTask,
  CallRecord,
  MemoryProfile,
  AuditLog
} from './types';

// Seed data
import {
  INITIAL_TENANTS,
  INITIAL_USERS,
  INITIAL_ROLES,
  INITIAL_PROMPTS,
  INITIAL_KNOWLEDGE,
  INITIAL_TASKS,
  INITIAL_RECORDS,
  INITIAL_MEMORIES,
  INITIAL_AUDITS
} from './data/mockDb';

// Subcomponents
import DashboardOverview from './components/DashboardOverview';
import PromptManager from './components/PromptManager';
import KnowledgeBaseManager from './components/KnowledgeBaseManager';
import CallCenterManager from './components/CallCenterManager';
import CallRecordsManager from './components/CallRecordsManager';
import MemorySystem from './components/MemorySystem';
import RBACSettings from './components/RBACSettings';

// Icons
import {
  Layers,
  HelpCircle,
  Database,
  PhoneCall,
  FileSpreadsheet,
  BrainCircuit,
  Lock,
  Globe2,
  Cpu,
  UserCheck,
  UserX,
  Plus,
  Compass,
  FileStack,
  Shield,
  Activity,
  Menu,
  Columns,
  Eye,
  EyeOff,
  LogOut
} from 'lucide-react';

export default function App() {
  // Master States
  const [tenants, setTenants] = useState<Tenant[]>(INITIAL_TENANTS);
  const [roles, setRoles] = useState<Role[]>(INITIAL_ROLES);
  const [users, setUsers] = useState<User[]>(INITIAL_USERS);
  const [prompts, setPrompts] = useState<PromptTemplate[]>(INITIAL_PROMPTS);
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>(INITIAL_KNOWLEDGE);
  const [tasks, setTasks] = useState<CallTask[]>(INITIAL_TASKS);
  const [records, setRecords] = useState<CallRecord[]>(INITIAL_RECORDS);
  const [memories, setMemories] = useState<MemoryProfile[]>(INITIAL_MEMORIES);
  const [audits, setAudits] = useState<AuditLog[]>(INITIAL_AUDITS);

  // Active configurations
  const [activeTenantId, setActiveTenantId] = useState<string>('tenant-fin');

  // User Authentication & Workspace selection state
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [pendingMatchingUsers, setPendingMatchingUsers] = useState<User[]>([]);
  const [loginInput, setLoginInput] = useState<string>('');
  const [passwordInput, setPasswordInput] = useState<string>('••••••••');
  const [showPassword, setShowPassword] = useState<boolean>(false);
  const [loginError, setLoginError] = useState<string>('');
  const [activeMenu, setActiveMenu] = useState<string>('overview');
  const [sidebarMode, setSidebarMode] = useState<'expanded' | 'collapsed' | 'hidden'>('expanded');
  const [sidebarWidth, setSidebarWidth] = useState<number>(256);
  const [isResizing, setIsResizing] = useState<boolean>(false);

  // Mouse drag handlers for resizing the sidebar and content areas
  const startResizing = React.useCallback((mouseDownEvent: React.MouseEvent) => {
    mouseDownEvent.preventDefault();
    setIsResizing(true);
  }, []);

  const stopResizing = React.useCallback(() => {
    setIsResizing(false);
  }, []);

  const resize = React.useCallback((mouseMoveEvent: MouseEvent) => {
    // Clamp width between 160px and 500px
    const newWidth = Math.max(160, Math.min(500, mouseMoveEvent.clientX));
    setSidebarWidth(newWidth);
  }, []);

  React.useEffect(() => {
    if (isResizing) {
      window.addEventListener('mousemove', resize);
      window.addEventListener('mouseup', stopResizing);
    } else {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    }
    return () => {
      window.removeEventListener('mousemove', resize);
      window.removeEventListener('mouseup', stopResizing);
    };
  }, [isResizing, resize, stopResizing]);
  
  // Platform Super-Admin View toggle
  const [isPlatformMode, setIsPlatformMode] = useState<boolean>(false);

  // --- Dynamic Live RBAC Persona Simulation ---
  // List of users in current active tenant
  const activeTenantUsers = users.filter(u => u.tenantId === activeTenantId);
  const [simulatedUserId, setSimulatedUserId] = useState<string>(activeTenantUsers[0]?.id || 'user-fin-1');
  
  // Resolve current active simulated user object and role
  const activeSimulatedUser = users.find(u => u.id === simulatedUserId) || activeTenantUsers[0] || users[0];
  
  // SUPPORT MERGING ROLES & PERMISSIONS FOR SAME TENANT GIVEN CURRENT USER
  const matchingActiveTenantUsers = currentUser
    ? users.filter(u => 
        u.tenantId === activeTenantId && 
        (u.email.toLowerCase() === currentUser.email.toLowerCase() || u.username.toLowerCase() === currentUser.username.toLowerCase())
      )
    : [activeSimulatedUser];

  // Resolve their combined roles and permissions
  const activeSimulatedRoles = roles.filter(r => 
    matchingActiveTenantUsers.some(u => u.roleId === r.id)
  );

  const activePermissions = Array.from(new Set(
    activeSimulatedRoles.flatMap(r => r.permissions)
  ));

  // Helper code checker to trigger padlocks or hide menus
  const hasPermission = (code: string): boolean => {
    // Platform mode bypasses normal tenant check controls (but let's represent standard permissions)
    if (isPlatformMode) return true;
    return activePermissions.includes(code);
  };

  // Switch tenant resets default users and active menus
  const handleTenantChange = (tenantId: string) => {
    setActiveTenantId(tenantId);
    setIsPlatformMode(false);
    
    // Auto preset corresponding user
    const matchedUsers = users.filter(u => u.tenantId === tenantId);
    if (matchedUsers.length > 0) {
      setSimulatedUserId(matchedUsers[0].id);
    }
    setActiveMenu('overview');
  };

  // --- Authentication Handlers ---
  const handleLoginSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!loginInput.trim()) return;

    // Find all user records where username or email matches loginInput (case-insensitive)
    const matches = users.filter(u => 
      u.username.toLowerCase() === loginInput.trim().toLowerCase() ||
      u.email.toLowerCase() === loginInput.trim().toLowerCase()
    );

    if (matches.length === 0) {
      setLoginError('账号或邮箱不存在，请核对输入或使用体验通道快速录入。');
      return;
    }

    if (matches.length === 1) {
      // Single tenant flow: immediately authenticates and routes
      const matchedUser = matches[0];
      setCurrentUser(matchedUser);
      handleTenantChange(matchedUser.tenantId);
      setSimulatedUserId(matchedUser.id);
      
      // Log audit
      const newLog: AuditLog = {
        id: `audit-${Date.now()}`,
        tenantId: matchedUser.tenantId,
        username: matchedUser.username,
        module: '登录服务',
        action: '单账户直达登录',
        ip: '118.42.50.211',
        createdAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
        details: `用户 ${matchedUser.realName} 登录，系统检测该用户为单租户架构，自动导入 ${matchedUser.tenantId} 模拟隔离沙箱。`
      };
      setAudits(prev => [newLog, ...prev]);
    } else {
      // Multi-tenant aggregate gateway flow: presents workspace selection
      setPendingMatchingUsers(matches);
    }
  };

  const handleQuickLogin = (usernameOrEmail: string) => {
    setLoginInput(usernameOrEmail);
    const matches = users.filter(u => 
      u.username.toLowerCase() === usernameOrEmail.toLowerCase() ||
      u.email.toLowerCase() === usernameOrEmail.toLowerCase()
    );

    if (matches.length === 1) {
      const matchedUser = matches[0];
      setCurrentUser(matchedUser);
      handleTenantChange(matchedUser.tenantId);
      setSimulatedUserId(matchedUser.id);
      
      const newLog: AuditLog = {
        id: `audit-${Date.now()}`,
        tenantId: matchedUser.tenantId,
        username: matchedUser.username,
        module: '登录服务',
        action: '一键极速登录',
        ip: '118.42.50.211',
        createdAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
        details: `单租户用户 ${matchedUser.realName} 极速通过免密体验通道登录。`
      };
      setAudits(prev => [newLog, ...prev]);
    } else if (matches.length > 1) {
      setPendingMatchingUsers(matches);
    }
  };

  const handleSelectWorkspace = (selectedUser: User) => {
    setCurrentUser(selectedUser);
    setPendingMatchingUsers([]);
    handleTenantChange(selectedUser.tenantId);
    setSimulatedUserId(selectedUser.id);

    const newLog: AuditLog = {
      id: `audit-${Date.now()}`,
      tenantId: selectedUser.tenantId,
      username: selectedUser.username,
      module: '多租户路由',
      action: '选择企业沙盒工作区',
      ip: '118.42.50.211',
      createdAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
      details: `多租户归属用户 ${selectedUser.realName} 通过路由网关自主进驻【${selectedUser.tenantId}】工作底盘，应用对应业务角色及权限授权。`
    };
    setAudits(prev => [newLog, ...prev]);
  };

  // --- CRUD State Mutators ---
  const handleAddPrompt = (prompt: PromptTemplate) => {
    setPrompts([...prompts, prompt]);
  };

  const handleUpdatePrompt = (prompt: PromptTemplate) => {
    setPrompts(prompts.map(p => p.id === prompt.id ? prompt : p));
  };

  const handleDeletePrompt = (id: string) => {
    setPrompts(prompts.filter(p => p.id !== id));
  };

  const handleAddKB = (kb: KnowledgeBase) => {
    setKnowledgeBases([...knowledgeBases, kb]);
  };

  const handleUpdateKB = (kb: KnowledgeBase) => {
    setKnowledgeBases(knowledgeBases.map(k => k.id === kb.id ? kb : k));
  };

  const handleDeleteKB = (id: string) => {
    setKnowledgeBases(knowledgeBases.filter(k => k.id !== id));
  };

  const handleAddTask = (task: CallTask) => {
    setTasks([...tasks, task]);
  };

  const handleUpdateTask = (task: CallTask) => {
    setTasks(tasks.map(t => t.id === task.id ? task : t));
  };

  const handleDeleteTask = (id: string) => {
    setTasks(tasks.filter(t => t.id !== id));
  };

  const handleUpdateRecord = (record: CallRecord) => {
    setRecords(records.map(r => r.id === record.id ? record : r));
  };

  const handleUpdateMemory = (profile: MemoryProfile) => {
    setMemories(memories.map(m => m.id === profile.id ? profile : m));
  };

  const handleAddRole = (role: Role) => {
    setRoles([...roles, role]);
  };

  const handleUpdateRole = (role: Role) => {
    setRoles(roles.map(r => r.id === role.id ? role : r));
  };

  const handleAddUser = (user: User) => {
    setUsers([...users, user]);
  };

  const handleUpdateUser = (user: User) => {
    setUsers(users.map(u => u.id === user.id ? user : u));
  };

  // Append customized operations log to auditing list
  const handleAddAuditLog = (module: string, action: string, details: string) => {
    const newLog: AuditLog = {
      id: `audit-${Date.now()}`,
      tenantId: activeTenantId,
      username: activeSimulatedUser?.username || 'system',
      module,
      action,
      ip: '118.42.50.211',
      createdAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
      details
    };
    setAudits([newLog, ...audits]);
  };

  // --- Platform Level Operations States ---
  const [newTenantName, setNewTenantName] = useState('');
  const [newTenantLogo, setNewTenantLogo] = useState('🏢');
  const [newTenantTier, setNewTenantTier] = useState<'Standard' | 'Enterprise' | 'Ultimate'>('Enterprise');

  // Trigger tenant creation on Platform level
  const handleCreateTenantOnPlatform = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTenantName.trim()) return;

    const newId = `tenant-${Date.now()}`;
    const newTenantObj: Tenant = {
      id: newId,
      name: newTenantName,
      logo: newTenantLogo,
      status: 'active',
      expiredAt: '2027-12-31',
      tier: newTenantTier,
      maxConcurrentCalls: newTenantTier === 'Ultimate' ? 400 : newTenantTier === 'Enterprise' ? 150 : 50,
      monthlyMinutesQuota: newTenantTier === 'Ultimate' ? 80000 : newTenantTier === 'Enterprise' ? 30000 : 10000,
      monthlyMinutesUsed: 0
    };

    setTenants([...tenants, newTenantObj]);

    // Automatically provision base template roles for this new tenant
    const adminRoleId = `role-${newId}-admin`;
    const baseRoles: Role[] = [
      {
        id: adminRoleId,
        tenantId: newId,
        name: '平台内置管理员',
        description: '系统开户默认授予权，全量读写。',
        permissions: roles.find(r => r.id === 'role-fin-admin')?.permissions || []
      },
      {
        id: `role-${newId}-operator`,
        tenantId: newId,
        name: '业务运营岗',
        description: '编辑提示词与线路调度。',
        permissions: roles.find(r => r.id === 'role-fin-operator')?.permissions || []
      }
    ];
    setRoles([...roles, ...baseRoles]);

    // Create default tenant admin member
    const newStaff: User = {
      id: `user-${newId}-admin`,
      tenantId: newId,
      username: `admin_${newTenantName.substring(0, 3)}`,
      realName: '企业主理人',
      email: `owner@${newId}.com`,
      roleId: adminRoleId,
      status: 'active',
      createdAt: new Date().toISOString().split('T')[0]
    };
    setUsers([...users, newStaff]);

    // Add general audit log
    const genericPlatformAudit: AuditLog = {
      id: `audit-${Date.now()}`,
      tenantId: 'platform_master',
      username: 'SuperAdmin',
      module: '超级平台管理',
      action: '签约开户租户',
      ip: '127.0.0.1',
      createdAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
      details: `成功签约部署多租户新实例 ${newTenantName}（${newTenantTier}套餐），预载入管理角色模板`
    };
    setAudits([genericPlatformAudit, ...audits]);

    setNewTenantName('');
    alert(`🎉 恭喜！多租户独立沙盒实例【${newTenantName}】注册成功并自动分拨模板角色！`);
  };

  const activeTenant = tenants.find(t => t.id === activeTenantId) || tenants[0];
  const userMatchedTenantsUsers = currentUser 
    ? users.filter(u => u.email.toLowerCase() === currentUser.email.toLowerCase() || u.username.toLowerCase() === currentUser.username.toLowerCase())
           .filter((value, index, self) => self.findIndex(t => t.tenantId === value.tenantId) === index)
    : [];

  if (!currentUser) {
    return (
      <div className="min-h-screen bg-slate-900 flex flex-col md:flex-row font-sans text-slate-100 selection:bg-indigo-500 selection:text-white">
        
        {/* Left decoration column: System intro */}
        <div className="md:w-1/2 bg-gradient-to-br from-slate-900 via-indigo-950 to-slate-950 p-8 md:p-16 flex flex-col justify-between border-b md:border-b-0 md:border-r border-slate-800 relative overflow-hidden shrink-0">
          {/* Ambient radial lighting ring effects */}
          <div className="absolute top-1/4 -left-1/4 w-96 h-96 bg-indigo-500/10 rounded-full blur-3xl pointer-events-none" />
          <div className="absolute bottom-1/4 -right-1/4 w-96 h-96 bg-emerald-500/5 rounded-full blur-3xl pointer-events-none" />

          {/* Logo Brand */}
          <div className="z-10 flex items-center gap-3">
            <span className="p-3 bg-indigo-600 rounded-2xl shadow-lg ring-1 ring-white/10">
              <Cpu className="w-7 h-7 text-white animate-spin-slow" />
            </span>
            <div>
              <h1 className="text-xl font-black tracking-tight bg-gradient-to-r from-white via-indigo-200 to-indigo-100 bg-clip-text text-transparent">
                MATRIX SAAS HUB
              </h1>
              <p className="text-[10px] text-indigo-400/80 font-mono tracking-widest uppercase">
                Enterprise Multi-Tenant Portal
              </p>
            </div>
          </div>

          {/* Slogans / Architecture details */}
          <div className="z-10 my-12 md:my-0 space-y-6 max-w-lg">
            <div className="space-y-3">
              <span className="text-[11px] font-bold text-indigo-300 bg-indigo-950/80 border border-indigo-800/60 px-3 py-1 rounded-full uppercase tracking-wider font-mono">
                SaaS Isolation Architecture
              </span>
              <h2 className="text-3xl md:text-4xl font-extrabold tracking-tight text-white leading-tight">
                智能外呼一体化 <br/>
                <span className="bg-gradient-to-r from-indigo-300 via-indigo-100 to-emerald-300 bg-clip-text text-transparent">
                  多租户物理隔离系统
                </span>
              </h2>
            </div>
            
            <p className="text-slate-400 text-sm leading-relaxed">
              系统采用深度解耦的独立租户工作沙盒设计，保障每一家签约企业在提示词版本演进、长期记忆沉淀、外呼并发线路、系统质检结果等多维度数据的极致隔离与绝对安全。
            </p>

            {/* Tech Grid stats */}
            <div className="grid grid-cols-2 gap-4 pt-4 border-t border-slate-800/80">
              <div className="bg-slate-950/40 p-3.5 rounded-xl border border-slate-800/60 font-mono">
                <span className="text-slate-500 text-[10px] block uppercase">MULTI-TENANCY</span>
                <span className="text-emerald-400 font-bold text-xs">✓ Sandbox Isolation</span>
              </div>
              <div className="bg-slate-950/40 p-3.5 rounded-xl border border-slate-800/60 font-mono">
                <span className="text-slate-500 text-[10px] block uppercase">ACCESS SECURITY</span>
                <span className="text-indigo-400 font-bold text-xs">✓ Granular RBAC Check</span>
              </div>
            </div>
          </div>

          {/* Footer of Intro */}
          <div className="z-10 text-[11px] text-slate-500 font-mono flex items-center justify-between">
            <span>© 2026 Matrix Intelligent Suite</span>
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 bg-emerald-500 rounded-full animate-pulse" />
              <span>Security Gateway Active</span>
            </span>
          </div>
        </div>

        {/* Right interaction column: Login Form OR Workspace aggregate Routing wizard */}
        <div className="flex-1 bg-slate-950 p-8 md:p-16 flex flex-col justify-center relative overflow-y-auto">
          <div className="max-w-md w-full mx-auto space-y-8">
            
            {pendingMatchingUsers.length === 0 ? (
              /* PANEL A: STANDARD LOGIN */
              <div className="space-y-6">
                <div>
                  <h3 className="text-2xl font-bold tracking-tight text-white">统一访问控制台</h3>
                  <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                    请输入您的账号或工作邮箱登入系统。系统将智能分析您的租户归属配置并提供对应的工作沙盒入口。
                  </p>
                </div>

                <form onSubmit={handleLoginSubmit} className="space-y-4">
                  {loginError && (
                    <div className="bg-red-950/60 border border-red-500/40 p-3 rounded-xl text-xs text-red-300 flex items-center gap-2">
                      <span>⚠️ {loginError}</span>
                    </div>
                  )}

                  <div className="space-y-1.5">
                    <label className="text-[11px] uppercase tracking-wider font-bold text-slate-400 block font-mono">
                      访问邮箱或成员名称 / Email or Username
                    </label>
                    <input
                      type="text"
                      value={loginInput}
                      onChange={(e) => {
                        setLoginInput(e.target.value);
                        setLoginError('');
                      }}
                      placeholder="请输入邮箱或系统成员名称..."
                      className="w-full bg-slate-900 border border-slate-800 rounded-xl px-4 py-3 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all font-sans"
                      required
                    />
                  </div>

                  <div className="space-y-1.5">
                    <label className="text-[11px] uppercase tracking-wider font-bold text-slate-400 block font-mono">
                      安全访问密码 / Access Password
                    </label>
                    <div className="relative">
                      <input
                        type={showPassword ? "text" : "password"}
                        value={passwordInput}
                        onChange={(e) => setPasswordInput(e.target.value)}
                        placeholder="••••••••"
                        className="w-full bg-slate-900 border border-slate-800 rounded-xl pl-4 pr-12 py-3 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 transition-all font-sans"
                        required
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute right-3.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors p-1"
                        title={showPassword ? "隐藏密码" : "显示密码"}
                      >
                        {showPassword ? (
                          <EyeOff className="w-4 h-4" />
                        ) : (
                          <Eye className="w-4 h-4" />
                        )}
                      </button>
                    </div>
                  </div>

                  <button
                    type="submit"
                    className="w-full cursor-pointer bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white font-bold py-3 px-4 rounded-xl text-sm transition-all shadow-md focus:outline-none focus:ring-2 focus:ring-indigo-500/50 flex items-center justify-center gap-2 mt-2"
                  >
                    <span>验证密码并登入系统</span>
                    <span className="text-lg">→</span>
                  </button>
                </form>

                {/* Direct Bypass shortcut menu */}
                <div className="relative my-6">
                  <div className="absolute inset-0 flex items-center">
                    <div className="w-full border-t border-slate-800/80" />
                  </div>
                  <div className="relative flex justify-center text-xs">
                    <span className="bg-slate-950 px-3 text-slate-500 font-semibold font-sans">企业模拟账户快速通道</span>
                  </div>
                </div>

                <div className="space-y-3">
                  <div className="text-[11px] text-slate-400 font-mono">点击下方内置人员，沉浸式体验不同租户归属架构：</div>
                  
                  <div className="grid grid-cols-1 gap-2.5">
                    {/* Multi-tenant selector account */}
                    <button
                      onClick={() => handleQuickLogin('wang20110277@gmail.com')}
                      className="text-left w-full cursor-pointer p-3.5 bg-slate-900/50 hover:bg-slate-900 rounded-xl border border-indigo-500/20 hover:border-indigo-500/60 transition-all group focus:outline-none"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <div className="flex items-center gap-2">
                            <span className="flex h-2 w-2 relative">
                              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75"></span>
                              <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
                            </span>
                            <span className="text-xs font-bold text-amber-400 group-hover:text-amber-300">王先生 (跨租户全栈超级运营官)</span>
                          </div>
                          <span className="text-[10px] text-slate-500 block truncate font-mono mt-0.5">
                            wang20110277@gmail.com
                          </span>
                        </div>
                        <span className="text-[10px] bg-indigo-950 text-indigo-300 px-2 py-1 rounded font-bold border border-indigo-800/30 shrink-0">
                          ⚡ 聚合 3 个租户
                        </span>
                      </div>
                    </button>

                    {/* Single-tenant account 1 */}
                    <button
                      onClick={() => handleQuickLogin('dr_chen')}
                      className="text-left w-full cursor-pointer p-3 bg-slate-900/30 hover:bg-slate-950 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all group focus:outline-none"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="text-xs font-semibold text-slate-300 group-hover:text-white">陈医生 (随访主管)</span>
                          <span className="text-[10px] text-slate-500 block font-mono mt-0.5">
                            dr_chen (归属于 智灵人民医院)
                          </span>
                        </div>
                        <span className="text-[10px] bg-slate-900 text-slate-400 px-2 py-1 rounded font-semibold border border-slate-800">
                          单租户直通
                        </span>
                      </div>
                    </button>

                    {/* Single-tenant account 2 */}
                    <button
                      onClick={() => handleQuickLogin('retail_admin')}
                      className="text-left w-full cursor-pointer p-3 bg-slate-900/30 hover:bg-slate-950 rounded-xl border border-slate-800/80 hover:border-slate-700 transition-all group focus:outline-none"
                    >
                      <div className="flex items-center justify-between">
                        <div>
                          <span className="text-xs font-semibold text-slate-300 group-hover:text-white">林经理 (售后负责人)</span>
                          <span className="text-[10px] text-slate-500 block font-mono mt-0.5">
                            retail_admin (归属于 乐尚精品售后商城)
                          </span>
                        </div>
                        <span className="text-[10px] bg-slate-900 text-slate-400 px-2 py-1 rounded font-semibold border border-slate-800">
                          单租户直通
                        </span>
                      </div>
                    </button>
                  </div>
                </div>
              </div>
            ) : (
              /* PANEL B: WORKSPACE ROUTING WIZARD */
              <div className="space-y-6">
                <div>
                  <span className="text-[10px] font-bold text-indigo-400 bg-indigo-950/80 border border-indigo-800/60 px-2.5 py-0.5 rounded-full uppercase tracking-wider font-mono">
                    Security Aggregate Routing
                  </span>
                  <h3 className="text-2xl font-bold tracking-tight text-white mt-2">多租户平台智能分流</h3>
                  <p className="text-xs text-slate-400 mt-1.5 leading-relaxed">
                    核验通过！检测到该统一访问账号在多个独立企业租户中配置了职务权限。请选择您想要登录的工作空间：
                  </p>
                </div>

                <div className="space-y-3 max-h-[360px] overflow-y-auto pr-1">
                  {(() => {
                    const uniqueTenantPendingUsers = pendingMatchingUsers.filter(
                      (value, index, self) => self.findIndex(t => t.tenantId === value.tenantId) === index
                    );

                    return uniqueTenantPendingUsers.map(u => {
                      const matchedTenant = tenants.find(t => t.id === u.tenantId);
                      if (!matchedTenant) return null;

                      // Get all roles matching for this tenant to display them as merged
                      const matchingUsersInTenant = pendingMatchingUsers.filter(userObj => userObj.tenantId === u.tenantId);
                      const combinedRoleNames = matchingUsersInTenant.map(userObj => {
                        const r = roles.find(roleObj => roleObj.id === userObj.roleId);
                        return r ? r.name : '企业成员';
                      }).join(' + ');

                      return (
                        <button
                          key={u.id}
                          onClick={() => handleSelectWorkspace(u)}
                          className="w-full text-left cursor-pointer p-4 rounded-xl bg-slate-900 border border-slate-800 hover:border-indigo-500 hover:bg-indigo-950/20 hover:shadow-lg transition-all group focus:outline-none flex gap-3.5 items-start"
                        >
                          <span className="text-2xl font-bold w-12 h-12 bg-slate-800 group-hover:bg-indigo-900 rounded-xl flex items-center justify-center transition-colors shrink-0 font-sans text-indigo-300">
                            {matchedTenant.logo}
                          </span>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <h4 className="text-sm font-bold text-slate-200 group-hover:text-white truncate">
                                {matchedTenant.name}
                              </h4>
                              <span className={`text-[9px] px-1.5 py-0.5 rounded font-mono font-bold border shrink-0 text-right ${
                                matchedTenant.tier === 'Ultimate' 
                                  ? 'bg-amber-950/60 text-amber-400 border-amber-800/40' 
                                  : matchedTenant.tier === 'Enterprise'
                                  ? 'bg-blue-950/60 text-blue-400 border-blue-800/40'
                                  : 'bg-slate-800 text-slate-400 border-slate-700'
                              }`}>
                                {matchedTenant.tier}
                              </span>
                            </div>
                            
                            <div className="flex items-center gap-1.5 mt-1 text-[11px] text-slate-400">
                              <span className="font-semibold text-indigo-400 font-sans">👥 绑定职务:</span>
                              <span className="truncate" title={combinedRoleNames}>{combinedRoleNames}</span>
                            </div>

                            {/* Workspaces resource limits indicators */}
                            <div className="mt-2.5 flex items-center gap-4 text-[10px] text-slate-500 font-mono border-t border-slate-800/60 pt-2 shrink-0">
                              <div>
                                并发限制: <span className="text-slate-300 font-semibold">{matchedTenant.maxConcurrentCalls} / 线</span>
                              </div>
                              <div>
                                额度用量: <span className="text-slate-300 font-semibold">{(matchedTenant.monthlyMinutesUsed / 1000).toFixed(1)}k 分</span>
                              </div>
                            </div>
                          </div>
                        </button>
                      );
                    });
                  })()}
                </div>

                <div className="pt-2">
                  <button
                    onClick={() => {
                      setPendingMatchingUsers([]);
                      setLoginError('');
                    }}
                    className="w-full text-center text-xs text-slate-400 hover:text-white py-2 font-semibold transition-colors flex items-center justify-center gap-1.5 focus:outline-none"
                  >
                    <span>← 返回常规账号登录</span>
                  </button>
                </div>
              </div>
            )}

            <div className="text-center text-xs text-slate-700 font-mono">
              Matrix Sandbox Cluster • Port: 3000 Active
            </div>

          </div>
        </div>

      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col font-sans text-slate-900 border-none">
      
      {/* 1. Dynamic RBAC Persona Simulator Banner (Tactile demonstration helper) */}
      <div className="bg-indigo-900 text-white py-3 px-4.5 text-xs flex flex-col md:flex-row justify-between items-start md:items-center gap-3 border-b border-indigo-950 z-20 shrink-0">
        <div className="flex items-center gap-2">
          <span className="bg-indigo-500/30 text-indigo-200 text-[10px] font-bold px-2 py-0.5 rounded border border-indigo-400/20 uppercase tracking-widest animate-pulse">
            RBAC 模拟身份切换器
          </span>
          <p className="font-sans text-slate-100">
            当期模拟租户: <strong className="text-amber-300 underline underline-offset-2">{activeTenant.name}</strong> • 正在模拟成员:
          </p>
        </div>

        {/* Persona Select */}
        <div className="flex flex-wrap items-center gap-2 text-xs w-full md:w-auto">
          {!isPlatformMode ? (
            <select
              value={simulatedUserId}
              onChange={e => {
                setSimulatedUserId(e.target.value);
                // Also trigger audit of simulation
                const targetU = users.find(ux => ux.id === e.target.value);
                const targetR = roles.find(rx => rx.id === targetU?.roleId);
                console.log("Simulating Persona to ", targetU?.realName, "with permissions", targetR?.permissions);
              }}
              className="bg-slate-800 text-white font-semibold border border-slate-700 rounded px-2.5 py-1 text-xs cursor-pointer focus:outline-none focus:ring-1 focus:ring-amber-300"
            >
              {activeTenantUsers.map(u => {
                const assignedR = roles.find(r => r.id === u.roleId);
                return (
                  <option key={u.id} value={u.id}>
                    👤 {u.realName} [{assignedR ? assignedR.name : '未分拨角色'}]
                  </option>
                );
              })}
            </select>
          ) : (
            <span className="bg-slate-800/80 border border-slate-700 text-amber-300 px-3 py-1 rounded font-bold">
              🛠️ 平台超级管理员 (全量穿梭白名单，不受RBAC策略制约)
            </span>
          )}

          <div className="w-px h-4 bg-slate-700/60 hidden md:block" />

          {/* Platform super mode switcher */}
          <button
            onClick={() => {
              setIsPlatformMode(!isPlatformMode);
              setActiveMenu('overview');
            }}
            className={`cursor-pointer px-3 py-1 rounded font-bold text-[11px] transition-colors ${
              isPlatformMode 
                ? 'bg-amber-400 text-slate-950 hover:bg-amber-300' 
                : 'bg-indigo-700 text-indigo-100 hover:bg-indigo-600 border border-indigo-600/30'
            }`}
          >
            {isPlatformMode ? '🔙 返回租户业务后台' : '⚙️ 进入平台超级管理员中枢'}
          </button>
        </div>
      </div>

      {/* 2. Top Portal Header bar */}
      <header className="bg-white border-b border-slate-150 h-16 flex items-center justify-between px-6 shrink-0 z-10 shadow-xs">
        {/* Left Side: Brand Identity (Uncompromised negative space) */}
        <div className="flex items-center gap-3">
          <span className="p-2.5 bg-indigo-600 text-white rounded-xl shadow-xs">
            <Cpu className="w-5 h-5 animate-spin-slow" />
          </span>
          <div>
            <span className="font-bold tracking-tight text-slate-900 text-base font-sans">智能外呼一体化后管系统</span>
            <span className="text-[10px] text-slate-400 block font-mono">Multi-Tenant & Granular RBAC Engine</span>
          </div>
        </div>

        {/* Right Side: Shared Operations Bar */}
        <div className="flex items-center gap-5">
          {/* Compact visual layout segmented controller for sidebar mode (no text, pure high-fidelity icons) */}
          <div className="flex items-center bg-slate-100 p-0.5 rounded-xl border border-slate-200/80 shadow-3xs gap-0.5" id="layout-controller">
            <button
              onClick={() => setSidebarMode('expanded')}
              className={`cursor-pointer p-2 rounded-lg transition-all flex items-center justify-center focus:outline-none ${
                sidebarMode === 'expanded' 
                  ? 'bg-white text-indigo-600 shadow-xs ring-1 ring-black/5' 
                  : 'text-slate-500 hover:bg-slate-200 hover:text-slate-800'
              }`}
              title="标准展开 (具备左右按住拖动改变宽度)"
              id="set-sidebar-expanded"
            >
              <Columns className="w-4 h-4" />
            </button>
            <button
              onClick={() => setSidebarMode('collapsed')}
              className={`cursor-pointer p-2 rounded-lg transition-all flex items-center justify-center focus:outline-none ${
                sidebarMode === 'collapsed' 
                  ? 'bg-white text-indigo-600 shadow-xs ring-1 ring-black/5' 
                  : 'text-slate-500 hover:bg-slate-200 hover:text-slate-800'
              }`}
              title="极简精简 (仅显示图标)"
              id="set-sidebar-collapsed"
            >
              <Menu className="w-4 h-4" />
            </button>
            <button
              onClick={() => setSidebarMode('hidden')}
              className={`cursor-pointer p-2 rounded-lg transition-all flex items-center justify-center focus:outline-none ${
                sidebarMode === 'hidden' 
                  ? 'bg-white text-indigo-600 shadow-xs ring-1 ring-black/5' 
                  : 'text-slate-500 hover:bg-slate-200 hover:text-slate-800'
              }`}
              title="完全隐藏菜单栏 (沉浸式全屏区)"
              id="set-sidebar-hidden"
            >
              <EyeOff className="w-4 h-4" />
            </button>
          </div>

          <div className="w-px h-6 bg-slate-200" />

          {/* User Multi-Tenant Workspace Swapper (Visible only if belongs to multiple tenants) */}
          {currentUser && !isPlatformMode && (
            <div className="flex items-center gap-2">
              {userMatchedTenantsUsers.length > 1 ? (
                <>
                  <span className="text-xs font-semibold text-slate-500 font-sans hidden md:inline">切换工作空间:</span>
                  <div className="flex items-center bg-slate-100 p-1 rounded-xl border border-slate-200/60 text-xs gap-1 shadow-3xs" id="user-subtenant-switcher">
                    {userMatchedTenantsUsers.map(u => {
                      const matTenant = tenants.find(t => t.id === u.tenantId);
                      const isSelected = u.tenantId === activeTenantId;
                      if (!matTenant) return null;
                      return (
                        <button
                          key={u.id}
                          onClick={() => {
                            setCurrentUser(u);
                            handleTenantChange(u.tenantId);
                            setSimulatedUserId(u.id);
                          }}
                          className={`cursor-pointer px-3 py-1.5 rounded-lg font-semibold flex items-center gap-1 transition-all ${
                            isSelected 
                              ? 'bg-white text-indigo-700 shadow-xs ring-1 ring-black/5 font-bold' 
                              : 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/50'
                          }`}
                          title={`进驻【${matTenant.name}】沙盒工作台`}
                        >
                          <span>{matTenant.logo}</span>
                          <span className="hidden sm:inline font-sans">{matTenant.name.substring(0, 4)}...</span>
                        </button>
                      );
                    })}
                  </div>
                </>
              ) : (
                /* Single tenant indicator badge highlighting sandbox isolation */
                <div className="flex items-center gap-1 text-[11px] bg-slate-50 border border-slate-200 text-slate-500 px-3 py-1.5 rounded-full font-sans">
                  <span className="text-emerald-500 font-bold font-sans">🔒</span>
                  <span className="font-semibold text-slate-600 font-sans text-xs">星河物理沙盒隔离安全节点</span>
                </div>
              )}
            </div>
          )}

          {/* User profile details with security logout */}
          {currentUser && (
            <>
              <div className="w-px h-6 bg-slate-200" />
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 bg-slate-50 p-1 rounded-full border border-slate-150 shrink-0">
                  <span className="w-8 h-8 bg-indigo-600 text-white font-bold rounded-full flex items-center justify-center text-xs shadow-xs uppercase font-sans">
                    {currentUser.realName.substring(0, 1)}
                  </span>
                  <div className="text-left hidden lg:block leading-none pr-3">
                    <div className="flex items-center gap-1.5 flex-wrap">
                      <span className="text-xs font-bold text-slate-800 block leading-tight">
                        {currentUser.realName.split(' ')[0]}
                      </span>
                      {activeSimulatedRoles.length > 0 && (
                        <div className="flex gap-1 flex-wrap">
                          {activeSimulatedRoles.map(r => (
                            <span 
                              key={r.id} 
                              className="text-[9px] font-bold bg-indigo-50 text-indigo-600 px-1.5 py-0.5 rounded border border-indigo-100 uppercase"
                              title={r.description}
                            >
                              {r.name}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                    <span className="text-[9px] text-slate-400 block font-mono leading-none mt-1.5">{currentUser.email}</span>
                  </div>
                </div>

                <button
                  onClick={() => {
                    setCurrentUser(null);
                    setPendingMatchingUsers([]);
                    setLoginInput('');
                    handleAddAuditLog('系统安全', '登出系统', `用户 ${currentUser.realName} 安全退出登录。`);
                  }}
                  className="cursor-pointer p-2 rounded-xl text-slate-400 hover:text-red-500 hover:bg-red-50 hover:border-red-200 border border-transparent transition-all shrink-0"
                  title="安全登出系统"
                >
                  <LogOut className="w-4.5 h-4.5" />
                </button>
              </div>
            </>
          )}
        </div>
      </header>

      {/* 3. Main Split Body */}
      <div className={`flex-1 flex overflow-hidden ${isResizing ? 'select-none' : ''}`}>
        
        {/* Sidebar */}
        <aside 
          id="system-main-sidebar"
          className={`bg-white border-slate-150 flex flex-col shrink-0 flex-nowrap overflow-hidden ${
            isResizing ? '' : 'transition-all duration-300'
          } ${sidebarMode !== 'hidden' ? 'border-r' : 'border-r-0'}`}
          style={{ 
            width: sidebarMode === 'expanded' ? `${sidebarWidth}px` : sidebarMode === 'collapsed' ? '68px' : '0px',
            opacity: sidebarMode === 'hidden' ? 0 : 1
          }}
        >
          <div className="h-full flex flex-col justify-between overflow-y-auto overflow-x-hidden" style={{ width: sidebarMode === 'expanded' ? '100%' : '68px' }}>
            <div className={sidebarMode === 'expanded' ? 'p-4 space-y-6' : 'p-2 space-y-4'}>
            
            {/* Active Workspace Label */}
            {sidebarMode === 'expanded' ? (
              <div className="bg-slate-50 p-3 rounded-xl border border-slate-200">
                <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider font-mono">
                  {isPlatformMode ? '平台超级管理控制台' : '租户隔离物理沙盒'}
                </span>
                <div className="flex items-center gap-1.5 mt-1">
                  <span className="text-xl">{isPlatformMode ? '⚙️' : activeTenant.logo}</span>
                  <span className="text-xs font-bold text-slate-800 truncate">
                    {isPlatformMode ? '全盘数据超级中心' : activeTenant.name}
                  </span>
                </div>
              </div>
            ) : (
              <div className="bg-slate-50 p-2.5 rounded-xl border border-slate-200 flex items-center justify-center cursor-pointer hover:bg-slate-100 transition-colors" title={isPlatformMode ? '全盘数据超级中心' : activeTenant.name}>
                <span className="text-xl">{isPlatformMode ? '⚙️' : activeTenant.logo}</span>
              </div>
            )}

            {/* Menu options mapped */}
            <div className="space-y-1.5">
              {sidebarMode === 'expanded' && (
                <span className="text-[10px] uppercase font-bold text-slate-400 tracking-wider font-mono block px-3 mb-2">
                  系统主干子菜单
                </span>
              )}
              
              {isPlatformMode ? (
                /* Platform Mode Sidebar Menus */
                <button 
                  onClick={() => setActiveMenu('overview')}
                  className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                    sidebarMode === 'expanded' 
                      ? 'w-full justify-between px-3 py-2.5 font-bold' 
                      : 'w-11 h-11 mx-auto justify-center'
                  } ${
                    activeMenu === 'overview' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                  }`}
                  title="租户套餐宏观配置"
                >
                  <div className="flex items-center gap-2">
                    <Globe2 className="w-5 h-5 shrink-0" />
                    {sidebarMode === 'expanded' && <span>租户套餐配置</span>}
                  </div>
                </button>
              ) : (
                /* Standard Tenant Mode Sidebar Menus with RBAC Lock Checks */
                <>
                  {/* Overview */}
                  <button 
                    onClick={() => setActiveMenu('overview')}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center'
                    } ${
                      activeMenu === 'overview' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    }`}
                    title="1. 数据总览大盘"
                  >
                    <div className="flex items-center gap-2">
                      <Compass className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">1. 数据总览大盘</span>}
                    </div>
                  </button>

                  {/* Prompts (menu:prompt) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:prompt')) setActiveMenu('prompts');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'prompts' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:prompt') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="2. 提示词管理"
                  >
                    <div className="flex items-center gap-2">
                      <FileStack className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">2. 提示词管理</span>}
                    </div>
                    {!hasPermission('menu:prompt') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>

                  {/* KB (menu:kb) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:kb')) setActiveMenu('kb');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'kb' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:kb') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="3. 知识库管理"
                  >
                    <div className="flex items-center gap-2">
                      <Database className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">3. 知识库管理</span>}
                    </div>
                    {!hasPermission('menu:kb') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>

                  {/* Call Center (menu:callCenter) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:callCenter')) setActiveMenu('callCenter');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'callCenter' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:callCenter') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="4. 呼叫中心"
                  >
                    <div className="flex items-center gap-2">
                      <PhoneCall className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">4. 呼叫中心</span>}
                    </div>
                    {!hasPermission('menu:callCenter') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>

                  {/* Call records CDR (menu:cdr) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:cdr')) setActiveMenu('cdr');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'cdr' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:cdr') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="5. 呼叫记录"
                  >
                    <div className="flex items-center gap-2">
                      <FileSpreadsheet className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">5. 呼叫记录</span>}
                    </div>
                    {!hasPermission('menu:cdr') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>

                  {/* Memory (menu:memory) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:memory')) setActiveMenu('memory');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'memory' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:memory') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="6. 记忆系统"
                  >
                    <div className="flex items-center gap-2">
                      <BrainCircuit className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">6. 记忆系统</span>}
                    </div>
                    {!hasPermission('menu:memory') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>

                  {/* Settings / RBAC (menu:settings) */}
                  <button 
                    onClick={() => {
                      if (hasPermission('menu:settings')) setActiveMenu('settings');
                    }}
                    className={`flex items-center rounded-lg text-xs cursor-pointer transition-colors ${
                      sidebarMode === 'expanded' 
                        ? 'w-full justify-between px-3 py-2.5 font-bold' 
                        : 'w-11 h-11 mx-auto justify-center relative'
                    } ${
                      activeMenu === 'settings' ? 'bg-indigo-50 text-indigo-700 font-bold' : 'text-slate-600 hover:bg-slate-50'
                    } ${!hasPermission('menu:settings') ? 'opacity-40 cursor-not-allowed bg-slate-50/50' : ''}`}
                    title="7. 权限管理"
                  >
                    <div className="flex items-center gap-2">
                      <Lock className="w-5 h-5 shrink-0" />
                      {sidebarMode === 'expanded' && <span className="truncate">7. 权限管理</span>}
                    </div>
                    {!hasPermission('menu:settings') && (
                      sidebarMode === 'expanded' ? (
                        <Lock className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      ) : (
                        <Lock className="absolute bottom-1 right-1 w-3 h-3 text-red-500 bg-white rounded-full p-0.5 border border-slate-200 shadow-2xs" />
                      )
                    )}
                  </button>
                </>
              )}

            </div>
          </div>

          {/* Sidebar Footer info */}
          {sidebarMode === 'expanded' ? (
            <div className="p-4 border-t border-slate-100 text-[10px] text-slate-400 text-left space-y-1 font-mono shrink-0">
              <div>
                <p className="truncate">系统容器状态: 🟢 ONLINE</p>
                <p className="truncate font-sans font-bold text-indigo-600 mt-1 cursor-col-resize select-none" title="您可以把鼠标悬浮于侧边栏右边缘，左右按住拖拽来调整系统宽度">↔ 边缘可拖动改变宽度</p>
              </div>
            </div>
          ) : (
            <div className="p-4 border-t border-slate-100 text-center text-xs text-emerald-500 shrink-0 flex flex-col items-center gap-2 justify-center" title="系统容器在线 (Gemini 1.5)">
              <div className="w-2.5 h-2.5 bg-emerald-500 rounded-full animate-pulse mx-auto" />
              <span className="text-[9px] text-slate-400">ON</span>
            </div>
          )}
          </div>

        </aside>

        {/* Resizable Divider Handle (Vertical Bar) */}
        {sidebarMode === 'expanded' && (
          <div
            onMouseDown={startResizing}
            className={`w-1 hover:w-1.5 focus:outline-none bg-slate-200 hover:bg-indigo-400 active:bg-indigo-600 cursor-col-resize h-full transition-all shrink-0 z-20 flex items-center justify-center group ${
              isResizing ? 'bg-indigo-500 w-1.5' : ''
            }`}
            title="左右拖拽缩放菜单区域"
          >
            <div className="w-0.5 h-8 bg-slate-300 group-hover:bg-indigo-500 rounded" />
          </div>
        )}

        {/* Content Pane Workspace */}
        <main className="flex-1 overflow-y-auto p-6 bg-slate-50 relative">
          
          {isPlatformMode ? (
            /* --- Platform Super Administration Console Matrix --- */
            <div className="space-y-6">
              
              <div className="bg-white p-5 rounded-xl border border-slate-150 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4 text-left">
                <div>
                  <h2 className="text-base font-bold text-slate-800 flex items-center gap-1.5 leading-none">
                    <Shield className="w-5 h-5 text-indigo-600" />
                    平台级别超级中枢控制中心 (Super-admin Core Panel)
                  </h2>
                  <p className="text-xs text-slate-500 mt-1.5">
                    面向SaaS系统运营人员。在此可任意创建、冻结企业租户，配置分配最高呼叫并发路数、包月分钟限制quota，并能看到跨租户级的全盘敏感审计。
                  </p>
                </div>
              </div>

              {/* Grid 2 Columns: left create new tenant / right active tenants details */}
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 text-left">
                
                {/* Left Form: create tenant (Col span 5/12) */}
                <div className="lg:col-span-5 bg-white rounded-xl border border-slate-100 shadow-xs p-5 space-y-3.5">
                  <h3 className="text-xs font-bold text-slate-800 border-b border-slate-100 pb-2 flex items-center gap-1">
                    <Plus className="w-4 h-4 text-emerald-500" />
                    签署开户全新租户 (Create SaaS Tenant Sandbox)
                  </h3>

                  <form onSubmit={handleCreateTenantOnPlatform} className="space-y-3.5 text-xs">
                    <div className="space-y-1">
                      <label className="text-slate-500 font-semibold block">企业主体全限定名</label>
                      <input
                        type="text"
                        placeholder="例：快手商家客诉服务中心"
                        value={newTenantName}
                        onChange={e => setNewTenantName(e.target.value)}
                        required
                        className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded focus:outline-none"
                      />
                    </div>

                    <div className="grid grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-slate-500 font-semibold block">外观Logo标识</label>
                        <select
                          value={newTenantLogo}
                          onChange={e => setNewTenantLogo(e.target.value)}
                          className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded focus:outline-none"
                        >
                          <option value="🏢">🏢 默认写字楼</option>
                          <option value="💰">💰 金融借贷</option>
                          <option value="🩺">🩺 医院随访</option>
                          <option value="🚗">🚗 汽车出行</option>
                          <option value="🚀">🚀 创业高新</option>
                        </select>
                      </div>

                      <div className="space-y-1">
                        <label className="text-slate-500 font-semibold block">划拨套餐类型配额</label>
                        <select
                          value={newTenantTier}
                          onChange={e => setNewTenantTier(e.target.value as any)}
                          className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded focus:outline-none"
                        >
                          <option value="Standard">Standard(标准版: 50路上限)</option>
                          <option value="Enterprise">Enterprise(企业版: 150路上限)</option>
                          <option value="Ultimate">Ultimate(尊特旗舰: 400路/分钟)</option>
                        </select>
                      </div>
                    </div>

                    <button
                      type="submit"
                      className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-bold py-2.5 px-4 rounded-lg cursor-pointer transition-colors"
                    >
                      ☑️ 核准划拨开户并生成基本RBAC模板角色
                    </button>
                  </form>
                </div>

                {/* Right Lists: Manage active tenants (Col span 7/12) */}
                <div className="lg:col-span-7 bg-white rounded-xl border border-slate-100 shadow-xs p-5 space-y-4">
                  <h3 className="text-xs font-bold text-slate-800 border-b border-slate-100 pb-2">已经承载并运转的企业租户 ({tenants.length}家)</h3>

                  <div className="space-y-3">
                    {tenants.map(t => {
                      const minutesLeft = Math.round((t.monthlyMinutesQuota - t.monthlyMinutesUsed) / 60);
                      return (
                        <div key={t.id} className="p-3.5 bg-slate-50 rounded-xl border border-slate-150 flex items-center justify-between text-xs font-mono">
                          <div className="space-y-0.5">
                            <h4 className="font-bold text-slate-800 text-[13px] font-sans flex items-center gap-1.5">
                              <span>{t.logo}</span>
                              {t.name}
                              <span className="text-[10px] bg-slate-200 px-1 rounded font-normal text-slate-500">{t.tier} 套餐</span>
                            </h4>
                            <p className="text-[10px] text-slate-400">
                              配额大纲上限并发：{t.maxConcurrentCalls}路并发 • 剩余：{minutesLeft} 小时/月
                            </p>
                          </div>

                          <div className="flex gap-2 items-center shrink-0">
                            {/* Toggle Suspend */}
                            <button
                              onClick={() => {
                                const nextStatus = t.status === 'active' ? 'suspended' : 'active';
                                setTenants(tenants.map(tx => tx.id === t.id ? { ...tx, status: nextStatus } : tx));
                                handleAddAuditLog('超管租户控制', '租户冻结或激活', `对租户 ${t.name} 实施了状态转换为 [${nextStatus}] 的操作`);
                              }}
                              className={`px-2.5 py-1 text-[10px] font-bold rounded border ${
                                t.status === 'active' ? 'bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100' : 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                              }`}
                            >
                              {t.status === 'active' ? '🔒 冻结租户' : '🔓 激活'}
                            </button>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

              </div>

              {/* Central Audit Trails across ALL tenants */}
              <div className="bg-white rounded-xl border border-slate-100 shadow-xs p-5 space-y-4 text-left">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="text-xs font-bold text-slate-850 flex items-center gap-1">
                    <Activity className="w-4 h-4 text-indigo-500 animate-pulse" />
                    SaaS全平台穿透级审计流水账 (Global Operations Audit Trails)
                  </h3>
                  <span className="text-[10px] text-slate-400">穿透全租户</span>
                </div>

                <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1 text-xs">
                  {audits.map(log => (
                    <div key={log.id} className="p-2.5 bg-slate-50 border border-slate-150 rounded flex justify-between gap-4 font-mono">
                      <div>
                        <span className="text-slate-400">[{log.createdAt}]</span>
                        <span className="text-indigo-700 font-bold ml-2">{log.username} :</span>
                        <span className="text-slate-800 font-medium ml-1 bg-white px-1.5 border border-slate-200 rounded text-[10px]">{log.module}</span>
                        <span className="text-slate-900 ml-2">{log.details}</span>
                      </div>
                      <span className="text-slate-500 text-[10px] shrink-0 font-sans font-bold uppercase">{log.tenantId.replace('tenant-', '')}</span>
                    </div>
                  ))}
                </div>
              </div>

            </div>
          ) : (
            /* --- standard Tenant Level Business Application Area --- */
            <>
              {activeMenu === 'overview' && (
                <DashboardOverview
                  currentTenant={activeTenant}
                  tasks={tasks}
                  records={records}
                  audits={audits}
                />
              )}

              {activeMenu === 'prompts' && (
                <PromptManager
                  prompts={prompts}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onAddPrompt={handleAddPrompt}
                  onUpdatePrompt={handleUpdatePrompt}
                  onDeletePrompt={handleDeletePrompt}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}

              {activeMenu === 'kb' && (
                <KnowledgeBaseManager
                  knowledgeBases={knowledgeBases}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onAddKB={handleAddKB}
                  onDeleteKB={handleDeleteKB}
                  onUpdateKB={handleUpdateKB}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}

              {activeMenu === 'callCenter' && (
                <CallCenterManager
                  tasks={tasks}
                  prompts={prompts}
                  knowledgeBases={knowledgeBases}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onAddTask={handleAddTask}
                  onUpdateTask={handleUpdateTask}
                  onDeleteTask={handleDeleteTask}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}

              {activeMenu === 'cdr' && (
                <CallRecordsManager
                  records={records}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onUpdateRecord={handleUpdateRecord}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}

              {activeMenu === 'memory' && (
                <MemorySystem
                  memories={memories}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onUpdateMemory={handleUpdateMemory}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}

              {activeMenu === 'settings' && (
                <RBACSettings
                  users={users}
                  roles={roles}
                  activeTenantId={activeTenantId}
                  hasPermission={hasPermission}
                  onAddRole={handleAddRole}
                  onUpdateRole={handleUpdateRole}
                  onAddUser={handleAddUser}
                  onUpdateUser={handleUpdateUser}
                  onAddAuditLog={handleAddAuditLog}
                />
              )}
            </>
          )}

        </main>
      </div>

    </div>
  );
}
