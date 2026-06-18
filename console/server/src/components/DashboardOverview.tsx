import React from 'react';
import { Tenant, CallTask, CallRecord, AuditLog } from '../types';
import { BarChart, Phone, ShieldCheck, Clock, Layers, HelpCircle, Activity, Heart, ShoppingBag, ExternalLink } from 'lucide-react';

interface DashboardOverviewProps {
  currentTenant: Tenant;
  tasks: CallTask[];
  records: CallRecord[];
  audits: AuditLog[];
}

export default function DashboardOverview({
  currentTenant,
  tasks,
  records,
  audits,
}: DashboardOverviewProps) {
  // Compute metrics based on current tenant
  const tenantTasks = tasks.filter(t => t.tenantId === currentTenant.id);
  const tenantRecords = records.filter(r => r.tenantId === currentTenant.id);
  const tenantAudits = audits.filter(a => a.tenantId === currentTenant.id);

  const totalCalls = tenantRecords.length;
  const connectedCalls = tenantRecords.filter(r => r.status === 'connected');
  const totalDuration = tenantRecords.reduce((sum, r) => sum + r.durationSeconds, 0);
  const totalCost = tenantRecords.reduce((sum, r) => sum + r.aiSpentCost, 0);

  const answerRate = totalCalls > 0 ? (connectedCalls.length / totalCalls) * 100 : 0;
  const avgDuration = connectedCalls.length > 0 ? Math.round(totalDuration / connectedCalls.length) : 0;

  // Render a lovely custom HTML layout
  return (
    <div className="space-y-6">
      {/* Top Welcome Panel with Tenant Stats */}
      <div className="bg-gradient-to-r from-slate-900 to-indigo-950 text-white rounded-2xl p-6 shadow-sm relative overflow-hidden">
        {/* Abstract background graphics */}
        <div className="absolute right-0 top-0 bottom-0 w-1/3 bg-radial-[circle_at_right_top] from-indigo-500/20 to-transparent pointer-events-none" />
        
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 z-10 relative">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-3xl">{currentTenant.logo}</span>
              <div>
                <h1 className="text-2xl font-semibold tracking-tight">
                  {currentTenant.name} <span className="text-xs bg-indigo-500/30 text-indigo-300 font-normal px-2.5 py-0.5 rounded-full ml-2 border border-indigo-400/20">{currentTenant.tier} 套餐</span>
                </h1>
                <p className="text-sm text-slate-300 mt-1">智能外呼后管大盘 • 独立沙盒沙盘展示</p>
              </div>
            </div>
          </div>
          
          <div className="flex gap-4 bg-slate-800/40 backdrop-blur-xs p-3 rounded-xl border border-slate-700/30 text-xs text-slate-300">
            <div>
              <p className="text-slate-400">并发限制</p>
              <p className="text-lg font-mono font-bold text-white mt-0.5">{currentTenant.maxConcurrentCalls} <span className="text-xs font-normal text-slate-400 font-sans">并发路数</span></p>
            </div>
            <div className="w-px bg-slate-700/60" />
            <div>
              <p className="text-slate-400">额度到期</p>
              <p className="text-lg font-mono font-bold text-emerald-400 mt-0.5">{currentTenant.expiredAt}</p>
            </div>
          </div>
        </div>

        {/* Quota Progress Bar */}
        <div className="mt-6 pt-4 border-t border-slate-800/50">
          <div className="flex justify-between text-xs text-slate-300 mb-2">
            <span>外呼额度消耗状况 ({Math.round(currentTenant.monthlyMinutesUsed / 60)} / {Math.round(currentTenant.monthlyMinutesQuota / 60)} 小时)</span>
            <span className="font-mono font-bold">{( (currentTenant.monthlyMinutesUsed / currentTenant.monthlyMinutesQuota) * 100 ).toFixed(1)}%</span>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-2.5 overflow-hidden">
            <div 
              className="bg-indigo-400 h-2.5 rounded-full transition-all duration-1000" 
              style={{ width: `${Math.min(100, (currentTenant.monthlyMinutesUsed / currentTenant.monthlyMinutesQuota) * 100)}%` }} 
            />
          </div>
        </div>
      </div>

      {/* 4 Cards Stats Grit */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-xs text-slate-500 font-medium">总呼出量 (试制)</p>
            <p className="text-2xl font-mono font-bold text-slate-900">{totalCalls}</p>
            <p className="text-[11px] text-slate-400">含忙音及拒接清单</p>
          </div>
          <div className="p-3 bg-indigo-50 text-indigo-600 rounded-lg">
            <Phone className="w-5 h-5" />
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-xs text-slate-500 font-medium">综合接通率</p>
            <p className="text-2xl font-mono font-bold text-emerald-600">{answerRate.toFixed(1)}%</p>
            <p className="text-[11px] text-slate-400">接通量: {connectedCalls.length} 个</p>
          </div>
          <div className="p-3 bg-emerald-50 text-emerald-600 rounded-lg">
            <BarChart className="w-5 h-5" />
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-xs text-slate-500 font-medium">平均通话时长</p>
            <p className="text-2xl font-mono font-bold text-amber-600">{avgDuration} <span className="text-xs font-sans text-slate-500">秒</span></p>
            <p className="text-[11px] text-slate-400">总时长: {totalDuration} 秒</p>
          </div>
          <div className="p-3 bg-amber-50 text-amber-600 rounded-lg">
            <Clock className="w-5 h-5" />
          </div>
        </div>

        <div className="bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-xs text-slate-500 font-medium">本月AI大模型算力费</p>
            <p className="text-2xl font-mono font-bold text-slate-800">¥ {totalCost.toFixed(2)}</p>
            <p className="text-[11px] text-slate-400">基于转写及Prompt tokens</p>
          </div>
          <div className="p-3 bg-slate-50 text-slate-600 rounded-lg">
            <Layers className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* Database Multi-Tenant Filter Monitor Diagram (Architectural Honesty) */}
      <div className="bg-slate-50 rounded-xl p-5 border border-slate-200">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-2 border-b border-slate-200/60 pb-3 mb-4">
          <div className="flex items-center gap-2">
            <div className="w-2.5 h-2.5 bg-indigo-500 rounded-full animate-pulse" />
            <span className="font-semibold text-slate-800 text-sm">数据库多租户数据隔离机制 (Security Filter Monitor)</span>
          </div>
          <span className="text-[11px] text-slate-500 bg-white border border-slate-200/80 px-2.5 py-0.5 rounded font-mono">
            隔离模式: 逻辑隔离 (单库单表 `tenant_id` 过滤)
          </span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="bg-slate-900 rounded-lg p-3.5 text-xs font-mono text-indigo-300 border border-slate-800">
            <span className="text-slate-500">// 后端持久层自动织入的租户拦截器代码 (MyBatis / TypeORM filter)</span>
            <div className="mt-1.5 space-y-1">
              <p><span className="text-pink-400">const</span> tenantId = jwtSession.<span className="text-yellow-300">getTenantId</span>(); <span className="text-slate-500">// 获取登录态 {currentTenant.id}</span></p>
              <p className="text-emerald-400 mt-2">
                SELECT <span className="text-white">*</span> FROM call_records
              </p>
              <p className="text-emerald-400">
                WHERE <span className="text-amber-400">tenant_id = '{currentTenant.id}'</span>
              </p>
              <p className="text-emerald-300">
                AND <span className="text-white">task_id = ?</span> LIMIT <span className="text-white">100</span>;
              </p>
            </div>
            <div className="mt-3 pt-3 border-t border-slate-800/80 text-[11px] text-slate-400 flex justify-between">
              <span>状态: 🛡️ 已开启物理租户校验过滤</span>
              <span className="text-emerald-400">COMPLIED</span>
            </div>
          </div>

          <div className="space-y-2 text-slate-600 text-xs leading-relaxed">
            <p className="text-slate-800 font-semibold flex items-center gap-1">如何确保租户隔离不越权？</p>
            <ul className="list-disc list-inside space-y-1 bg-white p-3 rounded-lg border border-slate-150 h-[100px] overflow-y-auto">
              <li><strong>行级数据隔离：</strong>数据库所有业务核心表内置索引字段 `tenant_id`，底层Dao框架统一通过切面自动续加该参数。</li>
              <li><strong>防越权拦截保护：</strong>管理员在前端下载录音、编辑提示词、或在后台做批量导入时，后端对单行资源的 `tenant_id` 归属权再次核对，若不符则强行爆出 403 Forbidden 不正当读取异常。</li>
              <li><strong>资源动态绑定：</strong>每次新外呼任务启动会自动绑定独立线路与独立的租户，独立记账。</li>
            </ul>
          </div>
        </div>
      </div>

      {/* Middle Grid: Task Lists & Log trace */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Task lists - Left 2 cols */}
        <div className="col-span-1 lg:col-span-2 bg-white rounded-xl border border-slate-100 shadow-xs p-5 space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-semibold text-slate-800 text-sm">进行中的任务监控栏</h3>
            <span className="text-xs text-indigo-600 font-medium">当前租户项目 ({tenantTasks.length})</span>
          </div>

          <div className="space-y-4 max-h-[300px] overflow-y-auto pr-1">
            {tenantTasks.length === 0 ? (
              <div className="text-center py-10 text-slate-400 text-xs">
                暂无配置中的外呼任务
              </div>
            ) : (
              tenantTasks.map(task => {
                const percent = Math.round((task.calledNumbers / task.totalNumbers) * 100);
                const connectRate = task.calledNumbers > 0 ? Math.round((task.connectedNumbers / task.calledNumbers) * 100) : 0;
                return (
                  <div key={task.id} className="p-3.5 bg-slate-50 rounded-lg border border-slate-100 flex flex-col md:flex-row gap-4 items-start md:items-center justify-between">
                    <div className="space-y-1 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${
                          task.status === 'running' ? 'bg-emerald-100 text-emerald-800' :
                          task.status === 'paused' ? 'bg-amber-100 text-amber-800' : 'bg-slate-200 text-slate-800'
                        }`}>
                          {task.status === 'running' ? '● 呼叫中' : task.status === 'paused' ? '|| 已挂起' : '已归档'}
                        </span>
                        <h4 className="text-xs font-bold text-slate-800 line-clamp-1">{task.name}</h4>
                      </div>
                      <p className="text-[11px] text-slate-500">并发限制: {task.concurrentLimit}路 • 拨号时段: {task.allowedHours}</p>
                      
                      {/* Progress bar */}
                      <div className="pt-2">
                        <div className="flex justify-between text-[10px] text-slate-400 mb-1">
                          <span>接通进度 比例</span>
                          <span>{task.calledNumbers} / {task.totalNumbers} （{percent}%）</span>
                        </div>
                        <div className="w-full bg-slate-200 rounded-full h-1.5 overflow-hidden">
                          <div className="bg-indigo-600 h-1.5 rounded-full" style={{ width: `${percent}%` }} />
                        </div>
                      </div>
                    </div>

                    <div className="flex gap-4 text-center md:border-l md:border-slate-200 md:pl-4 text-xs">
                      <div>
                        <p className="text-slate-400 text-[10px]">有效接通</p>
                        <p className="font-mono font-bold text-slate-800 mt-0.5">{task.connectedNumbers}</p>
                      </div>
                      <div>
                        <p className="text-slate-400 text-[10px]">接通率</p>
                        <p className="font-mono font-bold text-emerald-600 mt-0.5">{connectRate}%</p>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Audit Logs / Activity logs - Right Column */}
        <div className="bg-white rounded-xl border border-slate-100 shadow-xs p-5 space-y-4">
          <div className="flex justify-between items-center">
            <h3 className="font-semibold text-slate-800 text-sm">租户内操作追踪 (Audit Logs)</h3>
            <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded">实时审计</span>
          </div>

          <div className="space-y-3 max-h-[300px] overflow-y-auto pr-1">
            {tenantAudits.length === 0 ? (
              <div className="text-center py-10 text-slate-400 text-xs">
                暂无审计日志操作
              </div>
            ) : (
              tenantAudits.map(log => (
                <div key={log.id} className="text-xs border-b border-slate-100 pb-2.5 last:border-0 last:pb-0">
                  <div className="flex justify-between items-center">
                    <span className="font-medium text-indigo-600">{log.username}</span>
                    <span className="text-[10px] text-slate-400 font-mono">{log.createdAt.split(' ')[1]}</span>
                  </div>
                  <p className="text-slate-700 mt-0.5">
                    在 <span className="bg-slate-100 text-slate-700 px-1 py-0.2 rounded font-medium text-[11px]">{log.module}</span> 模块进行 <span className="text-slate-800 font-semibold">{log.action}</span>
                  </p>
                  <p className="text-[10px] text-slate-400 mt-0.5 leading-relaxed line-clamp-1">{log.details}</p>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* Guide Card to Product / tech managers */}
      <div className="bg-gradient-to-br from-amber-50 to-amber-100/50 rounded-xl p-5 border border-amber-200/60 flex flex-col md:flex-row items-start md:items-center gap-4">
        <div className="p-2.5 bg-amber-500 text-white rounded-lg">
          <ShieldCheck className="w-6 h-6" />
        </div>
        <div className="flex-1 space-y-1">
          <h4 className="font-bold text-amber-900 text-xs md:text-sm">💡 前置技术说明：您可以随时点击顶部「租户切算器」或「RBAC 模拟身份登录栏」！</h4>
          <p className="text-[11px] md:text-xs text-amber-800 leading-relaxed">
            该面板完全支持多租户逻辑隔绝和独立RBAC。例如：若切换到医疗租户，右侧审计日志、进行中外呼大表、知识切段召回沙箱将自动替换。若模拟选择<strong>“只读观察组”</strong>或<strong>“质检专员”</strong>，系统里对应的按钮如【新建提示词】、删除命令将自动下线遮罩或提示无权限。
          </p>
        </div>
      </div>
    </div>
  );
}
