import React, { useState } from 'react';
import { PromptTemplate, AuditLog } from '../types';
import { Plus, Edit3, Trash2, Copy, History, Terminal, Check, Info, ArrowLeft, RefreshCw, Layers } from 'lucide-react';

interface PromptManagerProps {
  prompts: PromptTemplate[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onAddPrompt: (prompt: PromptTemplate) => void;
  onUpdatePrompt: (prompt: PromptTemplate) => void;
  onDeletePrompt: (id: string) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

export default function PromptManager({
  prompts,
  activeTenantId,
  hasPermission,
  onAddPrompt,
  onUpdatePrompt,
  onDeletePrompt,
  onAddAuditLog,
}: PromptManagerProps) {
  const tenantPrompts = prompts.filter(p => p.tenantId === activeTenantId);

  // States
  const [selectedPrompt, setSelectedPrompt] = useState<PromptTemplate | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [viewHistoryPrompt, setViewHistoryPrompt] = useState<PromptTemplate | null>(null);

  // Form State
  const [formTitle, setFormTitle] = useState('');
  const [formCategory, setFormCategory] = useState('催收');
  const [formContent, setFormContent] = useState('');
  const [formVariables, setFormVariables] = useState<string[]>([]);
  const [formId, setFormId] = useState<string | null>(null);

  // Sandbox State
  const [activeSandboxPrompt, setActiveSandboxPrompt] = useState<PromptTemplate | null>(null);
  const [sandboxVariables, setSandboxVariables] = useState<{ [key: string]: string }>({});
  const [sandboxOutput, setSandboxOutput] = useState('');
  const [isSimulatingCall, setIsSimulatingCall] = useState(false);

  // Handle open create prompt form
  const handleOpenCreateFile = () => {
    if (!hasPermission('prompt:create')) return;
    setFormId(null);
    setFormTitle('');
    setFormCategory('催收');
    setFormContent('你是一家名为【企业名】的智能专属虚拟服务助手。你当前的呼叫对象是【{customer_name}】。\n\n出院提醒或服务告知规则详情如下：\n...');
    setFormVariables(['customer_name']);
    setIsEditing(true);
  };

  // Extract variables of prompt automatically using regex on {...}
  const handleContentChange = (val: string) => {
    setFormContent(val);
    const matches = val.match(/\{([^{}]+)\}/g);
    if (matches) {
      const vars = matches.map(m => m.replace(/[{}]/g, '')).filter((v, i, self) => self.indexOf(v) === i);
      setFormVariables(vars);
    } else {
      setFormVariables([]);
    }
  };

  // Save changes
  const handleSavePrompt = (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim() || !formContent.trim()) return;

    if (formId) {
      // Edit mode
      const original = prompts.find(p => p.id === formId)!;
      const updatedHistory = original.history ? [...original.history] : [];
      updatedHistory.unshift({
        version: original.version,
        content: original.content,
        updatedAt: original.updatedAt,
        updatedBy: original.updatedBy,
      });

      // Split version suffix
      const subVers = original.version.match(/V(\d+)\.(\d+)/);
      let nextVer = "V1.1";
      if (subVers) {
        const major = parseInt(subVers[1]);
        const minor = parseInt(subVers[2]) + 1;
        nextVer = `V${major}.${minor}`;
      }

      const updated: PromptTemplate = {
        ...original,
        title: formTitle,
        category: formCategory,
        content: formContent,
        variables: formVariables,
        version: nextVer,
        updatedAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
        updatedBy: '当前管理员',
        history: updatedHistory,
      };

      onUpdatePrompt(updated);
      onAddAuditLog('提示词管理', '修改提示词', `修改并在上级保存了新版本 ${nextVer} $${formTitle}`);
    } else {
      // New Mode
      const newPrompt: PromptTemplate = {
        id: `prompt-${Date.now()}`,
        tenantId: activeTenantId,
        title: formTitle,
        category: formCategory,
        content: formContent,
        variables: formVariables,
        version: 'V1.0',
        updatedAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
        updatedBy: '当前管理员',
        history: []
      };
      onAddPrompt(newPrompt);
      onAddAuditLog('提示词管理', '常见创建提示词', `新增提示词话术《${formTitle}》版本 V1.0`);
    }

    setIsEditing(false);
    setSelectedPrompt(null);
  };

  // Clone preset
  const handleClonePrompt = (tpl: PromptTemplate) => {
    if (!hasPermission('prompt:create')) return;
    const cloned: PromptTemplate = {
      ...tpl,
      id: `prompt-${Date.now()}`,
      title: `${tpl.title} (克隆版)`,
      version: 'V1.0',
      history: [],
      updatedAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
      updatedBy: '当前管理员'
    };
    onAddPrompt(cloned);
    onAddAuditLog('提示词管理', '克隆提示词', `克隆选定的话术，重新分株出《${cloned.title}》`);
  };

  // Open Edit Prompt Mode
  const handleOpenEdit = (prompt: PromptTemplate) => {
    if (!hasPermission('prompt:update')) return;
    setFormId(prompt.id);
    setFormTitle(prompt.title);
    setFormCategory(prompt.category);
    setFormContent(prompt.content);
    setFormVariables(prompt.variables);
    setIsEditing(true);
  };

  // Rollback historic version
  const handleRollback = (prompt: PromptTemplate, histItem: any) => {
    if (!hasPermission('prompt:update')) return;
    const originalHistory = [...(prompt.history || [])];
    const index = originalHistory.indexOf(histItem);
    if (index > -1) {
      originalHistory.splice(index, 1);
    }
    // Prepend current active state back to history
    originalHistory.unshift({
      version: prompt.version,
      content: prompt.content,
      updatedAt: prompt.updatedAt,
      updatedBy: prompt.updatedBy,
    });

    // Rollback to the historic content
    const updated: PromptTemplate = {
      ...prompt,
      content: histItem.content,
      version: `V${parseFloat(prompt.version.substring(1))}.01-b`,
      history: originalHistory,
      updatedAt: new Date().toISOString().replace('T', ' ').substring(0, 19),
      updatedBy: '退火操作员'
    };
    onUpdatePrompt(updated);
    onAddAuditLog('提示词管理', '回滚历史版本', `将《${prompt.title}》回滚恢复至其历史快照 V${histItem.version}`);
    setViewHistoryPrompt(null);
    setSelectedPrompt(updated);
  };

  // Trigger Sandbox simulation
  const handleLaunchSandbox = (prompt: PromptTemplate) => {
    setActiveSandboxPrompt(prompt);
    // Preset mock parameters of variables
    const initialVars: { [key: string]: string } = {};
    prompt.variables.forEach(v => {
      if (v === 'customer_name') initialVars[v] = '王小帅';
      else if (v === 'arrears_days') initialVars[v] = '7';
      else if (v === 'amount') initialVars[v] = '2500';
      else if (v === 'credit_limit') initialVars[v] = '15.5';
      else if (v === 'nurse_name') initialVars[v] = '刘护士';
      else if (v === 'product_name') initialVars[v] = '智能眼部按摩仪';
      else if (v === 'courier_name') initialVars[v] = '王丰顺';
      else initialVars[v] = '示例测试值';
    });
    setSandboxVariables(initialVars);
    setSandboxOutput('');
  };

  const runSimulation = () => {
    if (!activeSandboxPrompt) return;
    setIsSimulatingCall(true);
    setSandboxOutput('正在载入大语言算力模型，模拟格式化变量映射中...');

    setTimeout(() => {
      let speech = activeSandboxPrompt.content;
      // Replace variables
      Object.entries(sandboxVariables).forEach(([key, val]) => {
        speech = speech.replace(new RegExp(`\\{${key}\\}`, 'g'), val);
      });

      setSandboxOutput(`[系统已格式化 Prompt 构建，下面模拟 Gemini 极速生成 TTS 话务大段]：\n\n「语音流合成中」:\n` + speech.replace(/【[^】]+】/g, ''));
      setIsSimulatingCall(false);
    }, 1200);
  };

  return (
    <div className="space-y-6">
      {/* Overview and permission disclaimer */}
      <div className="flex justify-between items-center bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs">
        <div>
          <h2 className="text-base font-bold text-slate-800">提示词（话术模板）控制链</h2>
          <p className="text-xs text-slate-500 mt-1">负责集中构建、版本纠枉、并能通过变量映射测试大模型 TTS 实际发音语感。</p>
        </div>

        {/* Create Prompt Button (RBAC controlled) */}
        <button
          onClick={handleOpenCreateFile}
          disabled={!hasPermission('prompt:create')}
          className={`flex items-center gap-1 px-3.5 py-2 rounded-lg text-xs font-semibold cursor-pointer transition-colors ${
            hasPermission('prompt:create')
              ? 'bg-indigo-600 hover:bg-indigo-700 text-white'
              : 'bg-slate-100 text-slate-400 cursor-not-allowed'
          }`}
        >
          <Plus className="w-4 h-4" /> 新建提示词模板
        </button>
      </div>

      {/* Main Grid: Left is prompt card List, Right is Preview Details / Form / Sandbox */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Side: Prompts List (Col span 5/12) */}
        <div className="lg:col-span-4 bg-white rounded-xl border border-slate-100 p-4 space-y-3 shadow-xs h-[600px] overflow-y-auto">
          <div className="flex justify-between items-center border-b border-slate-100 pb-2 mb-2">
            <span className="text-xs font-bold text-slate-700">模板目录 ({tenantPrompts.length} 个)</span>
            <span className="text-[10px] text-slate-400 font-mono">租户隔离</span>
          </div>

          <div className="space-y-2.5">
            {tenantPrompts.length === 0 ? (
              <p className="text-slate-400 text-xs text-center py-10">租户名下暂无提示词模板</p>
            ) : (
              tenantPrompts.map(tpl => {
                const isSelected = selectedPrompt?.id === tpl.id;
                return (
                  <div
                    key={tpl.id}
                    onClick={() => {
                      setSelectedPrompt(tpl);
                      setIsEditing(false);
                    }}
                    className={`p-3.5 rounded-lg border text-left cursor-pointer transition-all ${
                      isSelected 
                        ? 'border-indigo-600 bg-indigo-50/20 shadow-xs' 
                        : 'border-slate-150 hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex justify-between items-start">
                      <span className="text-[10px] px-2 py-0.5 roundedbg text-[10px] font-semibold bg-indigo-100 text-indigo-700">
                        {tpl.category}
                      </span>
                      <span className="text-[10px] text-slate-400 font-mono">{tpl.version}</span>
                    </div>
                    <h3 className="text-xs font-bold text-slate-800 tracking-tight mt-1.5 line-clamp-1">{tpl.title}</h3>
                    <p className="text-[11px] text-slate-500 line-clamp-2 mt-1 leading-relaxed">{tpl.content}</p>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {tpl.variables.map(v => (
                        <span key={v} className="text-[9px] bg-slate-100 text-slate-600 border border-slate-200 rounded px-1">{`{${v}}`}</span>
                      ))}
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Right Side: Detail / Form / Testing Panel (Col span 8/12) */}
        <div className="lg:col-span-8 bg-white rounded-xl border border-slate-100 p-5 shadow-xs flex flex-col min-h-[600px]">
          
          {/* Form Mode */}
          {isEditing ? (
            <form onSubmit={handleSavePrompt} className="space-y-4 flex-1 flex flex-col justify-between">
              <div className="space-y-4">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="text-sm font-bold text-slate-800">{formId ? '编辑提示词话术' : '新增外呼提示词模型'}</h3>
                  <button type="button" onClick={() => setIsEditing(false)} className="text-xs text-slate-500 hover:text-slate-800">取消</button>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold">话术模板标题</label>
                    <input 
                      type="text" 
                      value={formTitle}
                      onChange={e => setFormTitle(e.target.value)}
                      placeholder="例：逾期还款提醒-温馨口径" 
                      className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600" 
                      required
                    />
                  </div>
                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold font-sans">话术核心分类</label>
                    <select
                      value={formCategory}
                      onChange={e => setFormCategory(e.target.value)}
                      className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600"
                    >
                      <option value="催收">催收 (Collections)</option>
                      <option value="意向回访">意向回访 (Leads Reactivation)</option>
                      <option value="满意度调查">满意度调查 (CSAT Survey)</option>
                    </select>
                  </div>
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-slate-500 font-semibold mb-1">
                    <span>系统级 Prompt 正文指导词</span>
                    <span className="text-[10px] text-emerald-600 font-sans">使用 {`{name}`} 键值系统将自动识别为动态变量</span>
                  </div>
                  <textarea 
                    value={formContent} 
                    onChange={e => handleContentChange(e.target.value)}
                    rows={12} 
                    className="w-full text-xs font-mono p-3 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-600 resize-none" 
                    required
                  />
                </div>

                {formVariables.length > 0 && (
                  <div className="space-y-1">
                    <span className="text-[11px] text-slate-500 font-medium h-6">已识别的动态参数变量：</span>
                    <div className="flex flex-wrap gap-1.5">
                      {formVariables.map(v => (
                        <span key={v} className="bg-amber-50 text-amber-800 text-[10px] font-mono border border-amber-200/50 px-2 py-0.5 rounded flex items-center">
                          {`{${v}}`}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="pt-4 border-t border-slate-100 flex justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setIsEditing(false)}
                  className="px-4 py-2 bg-slate-100 text-slate-600 text-xs rounded-lg hover:bg-slate-200 font-semibold"
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="px-4 py-2 bg-indigo-600 text-white text-xs rounded-lg hover:bg-indigo-700 font-semibold"
                >
                  提版本号保存并校验
                </button>
              </div>
            </form>
          ) : viewHistoryPrompt ? (
            /* History Viewer Section */
            <div className="flex-1 flex flex-col justify-between">
              <div className="space-y-4">
                <button 
                  onClick={() => setViewHistoryPrompt(null)}
                  className="flex items-center gap-1 text-xs text-slate-600 hover:text-indigo-600 font-medium"
                >
                  <ArrowLeft className="w-3.5 h-3.5" /> 返回话术详情
                </button>

                <h3 className="text-sm font-bold text-slate-800">《{viewHistoryPrompt.title}》的版本控制更迭日志</h3>
                
                <div className="border border-slate-150 rounded-lg divide-y divide-slate-100 overflow-hidden text-xs">
                  {viewHistoryPrompt.history && viewHistoryPrompt.history.length > 0 ? (
                    viewHistoryPrompt.history.map((hist, index) => (
                      <div key={index} className="p-4 bg-slate-50/50 space-y-2 text-left">
                        <div className="flex justify-between items-center text-[10px] text-slate-400">
                          <span>版本：<strong className="text-slate-700 font-mono">{hist.version}</strong></span>
                          <span>提交时间：{hist.updatedAt} • 修改人：{hist.updatedBy}</span>
                        </div>
                        <p className="bg-white p-2.5 rounded border border-slate-100 text-slate-600 font-mono text-[11px] max-h-[120px] overflow-y-auto whitespace-pre-wrap">
                          {hist.content}
                        </p>
                        <div className="flex justify-end gap-2">
                          <button
                            onClick={() => handleRollback(viewHistoryPrompt, hist)}
                            disabled={!hasPermission('prompt:update')}
                            className="bg-indigo-50 border border-indigo-200 text-indigo-700 text-[10px] px-2.5 py-1 rounded hover:bg-indigo-100 font-medium"
                          >
                            恢复应用此版本 (Rollback)
                          </button>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="py-12 text-center text-slate-400 font-sans">
                      暂无该话术历史更迭版本
                    </div>
                  )}
                </div>
              </div>
            </div>
          ) : selectedPrompt ? (
            /* Active prompt view context details */
            <div className="flex-1 flex flex-col justify-between">
              <div className="space-y-5">
                <div className="flex justify-between items-start border-b border-slate-100 pb-3">
                  <div>
                    <span className="text-[10px] px-2 py-0.5 rounded font-semibold bg-emerald-50 text-emerald-800 border border-emerald-200">
                      类别: {selectedPrompt.category}
                    </span>
                    <h3 className="text-sm font-bold text-slate-800 mt-2">{selectedPrompt.title}</h3>
                  </div>

                  <div className="flex gap-1.5">
                    {/* Clone (RBAC) */}
                    <button
                      onClick={() => handleClonePrompt(selectedPrompt)}
                      disabled={!hasPermission('prompt:create')}
                      title="克隆此套话术"
                      className="p-2 border border-slate-150 rounded-lg hover:bg-slate-50 text-slate-600 disabled:opacity-40"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                    {/* View history */}
                    <button
                      onClick={() => setViewHistoryPrompt(selectedPrompt)}
                      title="版本更迭记录"
                      className="p-2 border border-slate-150 rounded-lg hover:bg-slate-50 text-slate-600"
                    >
                      <History className="w-3.5 h-3.5" />
                    </button>
                    {/* Edit prompt (RBAC) */}
                    <button
                      onClick={() => handleOpenEdit(selectedPrompt)}
                      disabled={!hasPermission('prompt:update')}
                      title="编辑模板"
                      className="p-2 p bg-indigo-50 text-indigo-700 border border-indigo-150 rounded-lg hover:bg-indigo-100 disabled:opacity-40"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                    {/* Delete prompt (RBAC) */}
                    <button
                      onClick={() => {
                        if (hasPermission('prompt:delete')) {
                          onDeletePrompt(selectedPrompt.id);
                          onAddAuditLog('提示词管理', '删除提示词', `彻底移除了提示词话术《${selectedPrompt.title}》`);
                          setSelectedPrompt(null);
                        }
                      }}
                      disabled={!hasPermission('prompt:delete')}
                      title="删除模板"
                      className="p-2 border border-rose-150 text-rose-600 rounded-lg hover:bg-rose-50 disabled:opacity-40"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Content body with pretty design highlighting */}
                <div className="bg-slate-50 border border-slate-200 p-4 rounded-xl space-y-4">
                  <div className="flex justify-between items-center text-xs text-slate-400 font-mono">
                    <span>版本: {selectedPrompt.version} • 由 {selectedPrompt.updatedBy} 自检</span>
                    <span>更新时间: {selectedPrompt.updatedAt}</span>
                  </div>
                  <p className="text-xs font-mono text-slate-700 leading-relaxed whitespace-pre-wrap">
                    {selectedPrompt.content}
                  </p>
                </div>

                {/* Dynamic variables checker */}
                {selectedPrompt.variables.length > 0 && (
                  <div className="space-y-1.5 p-3.5 bg-blue-50/40 rounded-lg border border-blue-100/60">
                    <span className="text-[11px] text-blue-800 font-semibold block">已绑定的字段，当自动发起外呼时将自动注入:</span>
                    <div className="flex flex-wrap gap-1.5">
                      {selectedPrompt.variables.map(v => (
                        <span key={v} className="text-[10px] font-mono bg-blue-100/60 border border-blue-200 text-blue-800 px-2 py-0.5 rounded leading-none">
                          {`customer_list.${v}`}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              {/* Bot panel: Launch testing sandbox */}
              <div className="mt-6 pt-4 border-t border-slate-100">
                <button
                  onClick={() => handleLaunchSandbox(selectedPrompt)}
                  className="w-full flex items-center justify-center gap-2 bg-slate-900 text-white p-3.5 rounded-xl text-xs font-bold hover:bg-indigo-950 transition-colors"
                >
                  <Terminal className="w-4 h-4" /> 发起智能外呼 AI 模型发言联调沙箱 (Variable Sandbox Simulator)
                </button>
              </div>
            </div>
          ) : (
            /* Blank State */
            <div className="flex-1 flex flex-col items-center justify-center text-center space-y-3 p-10 select-none">
              <Layers className="w-12 h-12 text-slate-300 stroke-1" />
              <div className="space-y-1">
                <h4 className="text-slate-800 font-semibold text-xs">暂无选定的话术提示词模板</h4>
                <p className="text-[11px] text-slate-400 max-w-sm">
                  请从左侧栏列表或内置分类中，点击一个外呼Prompt进行审核、克隆多级版本、回滚或召集沙盒调试。
                </p>
              </div>
            </div>
          )}

          {/* Sandbox interactive popover overlay when active */}
          {activeSandboxPrompt && (
            <div className="fixed inset-0 bg-slate-950/60 backdrop-blur-xs flex items-center justify-center z-50 p-4">
              <div className="bg-white rounded-2xl border border-slate-100 max-w-2xl w-full p-6 shadow-2xl flex flex-col space-y-5 animate-in fade-in zoom-in-95 duration-200 text-left">
                <div className="flex justify-between items-center border-b border-slate-100 pb-3">
                  <div className="flex items-center gap-2">
                    <Terminal className="w-4 h-4 text-indigo-600" />
                    <h3 className="text-sm font-bold text-slate-800">外呼 AI 发音沙箱 (Prompt Test Workspace)</h3>
                  </div>
                  <button 
                    onClick={() => setActiveSandboxPrompt(null)} 
                    className="text-xs bg-slate-100 hover:bg-slate-200 px-2 py-1 rounded text-slate-500 hover:text-slate-800 font-semibold"
                  >
                    关闭
                  </button>
                </div>

                <p className="text-[11px] text-slate-500 leading-relaxed bg-indigo-50 border border-indigo-100 text-indigo-800 p-2.5 rounded-lg">
                  当前话术: <strong>《{activeSandboxPrompt.title}》</strong>。您可在此输入多租户自定义的测试变量数据，平台将模拟把提示词格式化后输送予大语言语音流系统所得到的实际口头音色。
                </p>

                {/* Grid of Inputs */}
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-h-[160px] overflow-y-auto pr-1">
                  {activeSandboxPrompt.variables.length === 0 ? (
                    <p className="text-slate-400 text-xs py-2 col-span-2 text-center">此话术无需填充定制动态变量</p>
                  ) : (
                    activeSandboxPrompt.variables.map(v => (
                      <div key={v} className="space-y-1">
                        <label className="text-[10px] text-slate-500 font-bold font-mono">{`参数 {${v}}`}</label>
                        <input
                          type="text"
                          value={sandboxVariables[v] || ''}
                          onChange={e => setSandboxVariables({ ...sandboxVariables, [v]: e.target.value })}
                          className="w-full text-xs p-2 bg-slate-50 border border-slate-150 rounded"
                        />
                      </div>
                    ))
                  )}
                </div>

                {/* Actions Trigger */}
                <button
                  onClick={runSimulation}
                  disabled={isSimulatingCall}
                  className="w-full bg-indigo-600 text-white text-xs font-bold p-3 rounded-lg hover:bg-indigo-700 disabled:opacity-50 flex items-center justify-center gap-1.5 transition-colors"
                >
                  {isSimulatingCall ? (
                    <>
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" /> 大语料多层特征合成汇编中...
                    </>
                  ) : (
                    '💡 发起大模型联调测试生成发言结果'
                  )}
                </button>

                {/* Simulation Output Terminal */}
                {sandboxOutput && (
                  <div className="bg-slate-900 border border-slate-800 rounded-lg p-4 font-mono text-xs text-indigo-400 h-[180px] overflow-y-auto">
                    <p className="whitespace-pre-wrap">{sandboxOutput}</p>
                  </div>
                )}
              </div>
            </div>
          )}

        </div>

      </div>
    </div>
  );
}
