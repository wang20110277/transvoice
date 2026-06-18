import React, { useState } from 'react';
import { MemoryProfile, AuditLog } from '../types';
import { BrainCircuit, Search, HeartCrack, Activity, Tags, ShieldAlert, KeyRound, Save, Plus, Trash2 } from 'lucide-react';

interface MemorySystemProps {
  memories: MemoryProfile[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onUpdateMemory: (profile: MemoryProfile) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

export default function MemorySystem({
  memories,
  activeTenantId,
  hasPermission,
  onUpdateMemory,
  onAddAuditLog,
}: MemorySystemProps) {
  const tenantMemories = memories.filter(m => m.tenantId === activeTenantId);

  // Search profiles state
  const [searchPhone, setSearchPhone] = useState('');
  const [selectedProfile, setSelectedProfile] = useState<MemoryProfile | null>(tenantMemories[0] || null);

  // Intent variable extract rules
  const [extractionRules, setExtractionRules] = useState([
    { key: 'repay_hardship_factor', desc: '客户自述导致借款滞延的实质财务因由', type: 'String' },
    { key: 'preferred_contact_time', desc: '客户口述的推荐最佳免打扰接线时区', type: 'String' },
    { key: 'diet_mistake_warnings', desc: '是否存在禁忌食物错误常识误区', type: 'Boolean' },
    { key: 'cloth_resizing_desired', desc: '衣服更换的目标目标尺码类型', type: 'String' }
  ]);
  const [newRuleKey, setNewRuleKey] = useState('');
  const [newRuleValue, setNewRuleValue] = useState('');

  // Editable memory variables mapping state
  const [isEditingMemory, setIsEditingMemory] = useState(false);
  const [memoryEditBuffer, setMemoryEditBuffer] = useState<{ [key: string]: string }>({});

  const handleStartEditMemory = (profile: MemoryProfile) => {
    if (!hasPermission('memory:edit')) return;
    setMemoryEditBuffer({ ...profile.longTermMemory });
    setIsEditingMemory(true);
  };

  const handleSaveMemoryEdit = (profile: MemoryProfile) => {
    if (!hasPermission('memory:edit')) return;

    const updated: MemoryProfile = {
      ...profile,
      longTermMemory: memoryEditBuffer
    };

    onUpdateMemory(updated);
    setSelectedProfile(updated);
    setIsEditingMemory(false);
    onAddAuditLog('记忆系统', '修改客户记忆', `人工干预矫正了电话号 ${profile.phone} 的长期固有数据库记忆。`);
    alert('💾 长期合规特征集已人工校正并重新归档！');
  };

  const handleCreateExtractRule = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRuleKey.trim()) return;
    setExtractionRules([...extractionRules, { key: newRuleKey, desc: newRuleValue || '自定义提取字段描述', type: 'String' }]);
    setNewRuleKey('');
    setNewRuleValue('');
    onAddAuditLog('记忆系统', '制定提取规则', `新增了外呼会话变量自萃取指令：[${newRuleKey}]`);
  };

  const filteredMemories = tenantMemories.filter(m => 
    m.phone.includes(searchPhone) || m.customerName.includes(searchPhone)
  );

  return (
    <div className="space-y-6">
      
      {/* Overview */}
      <div className="bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-800 flex items-center gap-1.5">
            <BrainCircuit className="w-5 h-5 text-indigo-600" />
            AI RAG 记忆与客户长期画像系统 (Memory Hub)
          </h2>
          <p className="text-xs text-slate-500 mt-1">负责管束智能呼叫机器人自动爬梳总结所得的信息。支持纠正可能侵犯隐私的数据项保护（GDPR Right to Rectification）。</p>
        </div>

        <span className="text-xs text-slate-400 bg-slate-50 border border-slate-200 px-3 py-1.5 rounded-lg font-semibold">
          本月累计智能提取记忆项：{tenantMemories.reduce((sum, m) => sum + Object.keys(m.longTermMemory).length, 0)} 条
        </span>
      </div>

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Subsection: Profiles directory (Col span 4/12) */}
        <div className="lg:col-span-4 bg-white rounded-xl border border-slate-100 p-4 shadow-xs h-[600px] flex flex-col justify-between">
          <div className="space-y-3 flex-1 flex flex-col overflow-hidden">
            <div className="border-b border-slate-100 pb-2">
              <span className="text-xs font-bold text-slate-700">客户档案查找 (Phone Profiles)</span>
            </div>

            {/* Input Search */}
            <div className="relative">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
              <input
                type="text"
                placeholder="搜索电话手机 / 对应姓名..."
                value={searchPhone}
                onChange={e => setSearchPhone(e.target.value)}
                className="w-full text-xs p-2.5 pl-9 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none"
              />
            </div>

            {/* Profiles stack */}
            <div className="flex-1 overflow-y-auto pr-1 space-y-2.5 mt-2.5 text-left">
              {filteredMemories.map(mem => {
                const isActive = selectedProfile?.id === mem.id;
                return (
                  <div
                    key={mem.id}
                    onClick={() => {
                      setSelectedProfile(mem);
                      setIsEditingMemory(false);
                    }}
                    className={`p-3 rounded-lg border cursor-pointer transition-all ${
                      isActive ? 'border-indigo-600 bg-indigo-50/10' : 'border-slate-150 hover:bg-slate-50'
                    }`}
                  >
                    <div className="flex justify-between text-xs items-center">
                      <h4 className="font-bold text-slate-800">{mem.customerName} <span className="font-mono text-slate-500">[{mem.gender}]</span></h4>
                      <span className="text-[10px] text-slate-400 font-mono">{mem.phone}</span>
                    </div>
                    <p className="text-[10px] text-slate-500 mt-1 line-clamp-1">{mem.lastIntent}</p>
                    <div className="flex flex-wrap gap-1 mt-1.5">
                      {mem.tags.map(t => (
                        <span key={t} className="text-[8px] bg-slate-100 text-slate-600 border border-slate-250 px-1 py-0.2 rounded font-semibold">{t}</span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right Subsection Details Panel & Auto schemes (Col span 8/12) */}
        <div className="lg:col-span-8 flex flex-col gap-6">
          {selectedProfile ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex-1">
              {/* Left Column in Detail - Memory rectification */}
              <div className="space-y-4">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="text-xs font-bold text-slate-800">1. 长效记忆自持元数据 (Long-term Memory JSON)</h3>
                  
                  {/* EDIT Action (RBAC check) */}
                  {!isEditingMemory ? (
                    <button
                      onClick={() => handleStartEditMemory(selectedProfile)}
                      disabled={!hasPermission('memory:edit')}
                      className={`text-[10px] font-bold px-2 py-1 rounded border transition-all ${
                        hasPermission('memory:edit') 
                          ? 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100' 
                          : 'bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed'
                      }`}
                    >
                      ✏️ 纠偏记忆修正
                    </button>
                  ) : (
                    <button
                      onClick={() => handleSaveMemoryEdit(selectedProfile)}
                      className="text-[10px] font-bold px-2.5 py-1 bg-emerald-600 text-white rounded hover:bg-emerald-700 flex items-center gap-1.5"
                    >
                      <Save className="w-3 h-3" /> 保存订正
                    </button>
                  )}
                </div>

                {/* Edit Form or read only display */}
                {isEditingMemory ? (
                  <div className="space-y-3 max-h-[350px] overflow-y-auto pr-1">
                    <div className="p-2 bg-amber-50 rounded border border-amber-200 text-[10px] text-amber-800 leading-relaxed font-sans mb-1">
                      ⚠️ 隐私数据纠偏模式：您可对提取出错、或客户主张删除（GDPR 被遗忘权）的字段特征内容手工修改重置。
                    </div>
                    {Object.entries(memoryEditBuffer).map(([key, val]) => (
                      <div key={key} className="space-y-1">
                        <label className="text-[10px] font-mono text-slate-500 font-bold block">{key}</label>
                        <input
                          type="text"
                          value={val}
                          onChange={e => setMemoryEditBuffer({ ...memoryEditBuffer, [key]: e.target.value })}
                          className="w-full text-xs p-2 bg-slate-50 border border-slate-250 rounded font-mono"
                        />
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="space-y-3">
                    <pre className="bg-slate-900 border border-slate-800 text-indigo-300 font-mono text-[11px] p-4 rounded-xl leading-relaxed max-h-[250px] overflow-y-auto text-left">
                      {JSON.stringify(selectedProfile.longTermMemory, null, 2)}
                    </pre>

                    <div className="p-3 bg-indigo-50/50 border border-indigo-100 rounded-lg flex items-start gap-2.5 text-[11px] text-slate-600 text-left">
                      <KeyRound className="w-4 h-4 text-indigo-500 shrink-0 mt-0.5" />
                      <div>
                        <p className="font-bold text-indigo-900">大模型意图提取依据：</p>
                        <p className="mt-0.5 font-mono">当AI与该客户【{selectedProfile.customerName}】通话时，这些变量将作为系统默认上下文一并载入，协助AI识别历史承诺。</p>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Right Column in Detail - Interactive history summaries logs */}
              <div className="space-y-4 border-t md:border-t-0 md:border-l md:pl-6 border-slate-150 text-left">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="text-xs font-bold text-slate-800">2. 会话级摘要流水堆 (Sequential Audits)</h3>
                  <span className="text-[10px] text-slate-400">总历次：{selectedProfile.sessionMemoryLogs.length} 回</span>
                </div>

                <div className="space-y-3.5 max-h-[380px] overflow-y-auto pr-1">
                  {selectedProfile.sessionMemoryLogs.map((log, index) => (
                    <div key={index} className="p-3 bg-slate-50/70 border border-slate-150 rounded-lg text-xs space-y-1">
                      <div className="flex justify-between items-center font-mono text-[10px] text-slate-400">
                        <span>外呼任务序列 ID</span>
                        <span>{log.timestamp}</span>
                      </div>
                      <p className="text-slate-700 leading-relaxed font-mono font-medium text-[11px] pt-1">
                        {log.summary}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <p className="text-slate-400 text-xs text-center py-20 bg-white border rounded">暂无可用记忆档案</p>
          )}

          {/* Prompt memory variables auto-extract directives config list */}
          <div className="bg-white rounded-xl border border-slate-100 p-4.5 shadow-xs text-left">
            <h4 className="text-xs font-bold text-slate-800 border-b border-slate-100 pb-2 mb-3.5 flex items-center gap-1.5">
              <Tags className="w-4 h-4 text-indigo-600" />
              <span>大模型话语流语义抽取键策略 (Information Extract Guidelines Schema)</span>
            </h4>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="space-y-2 max-h-[160px] overflow-y-auto pr-1">
                {extractionRules.map(rule => (
                  <div key={rule.key} className="p-2 bg-slate-50 border border-slate-150 rounded text-xs flex justify-between gap-2">
                    <div>
                      <span className="font-mono font-bold text-indigo-700 bg-indigo-50 border border-indigo-200 px-1 rounded text-[10px]">{rule.key}</span>
                      <p className="text-[10px] text-slate-500 mt-1">{rule.desc}</p>
                    </div>
                    <span className="text-[9px] text-slate-400 shrink-0 font-bold font-mono">{rule.type}</span>
                  </div>
                ))}
              </div>

              {/* Create new variable guideline rules */}
              <div className="space-y-2 p-3 bg-slate-50 rounded border border-slate-200">
                <span className="text-[10px] font-bold text-slate-500 uppercase block">定制写回规则策略</span>
                <form onSubmit={handleCreateExtractRule} className="space-y-2 text-xs">
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      placeholder="存储变量键: 例 family_help"
                      value={newRuleKey}
                      onChange={e => setNewRuleKey(e.target.value)}
                      required
                      className="w-full text-[11px] p-2 bg-white border border-slate-200 rounded font-mono focus:outline-none"
                    />
                    <button
                      type="submit"
                      className="w-full bg-slate-900 border border-slate-800 text-white font-bold p-1 rounded-md text-[10px] hover:bg-black transition-colors shrink-0"
                    >
                      + 部署语义自拉取
                    </button>
                  </div>
                  <input
                    type="text"
                    placeholder="简述如何引导AI提炼：例‘客户是否有提及家人辅佐协助还款’"
                    value={newRuleValue}
                    onChange={e => setNewRuleValue(e.target.value)}
                    className="w-full text-[11px] p-2 bg-white border border-slate-200 rounded focus:outline-none"
                  />
                </form>
              </div>
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
