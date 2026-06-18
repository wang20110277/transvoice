import React, { useState, useEffect } from 'react';
import { CallRecord, ChatMessage, AuditLog } from '../types';
import { Play, Pause, Search, ClipboardCheck, ArrowUpRight, Volume2, ShieldAlert, BadgeCheck, FileDown, Smile, AlertTriangle, Eye } from 'lucide-react';

interface CallRecordsManagerProps {
  records: CallRecord[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onUpdateRecord: (record: CallRecord) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

export default function CallRecordsManager({
  records,
  activeTenantId,
  hasPermission,
  onUpdateRecord,
  onAddAuditLog,
}: CallRecordsManagerProps) {
  const tenantRecords = records.filter(r => r.tenantId === activeTenantId);

  // Filters
  const [filterPhone, setFilterPhone] = useState('');
  const [filterStatus, setFilterStatus] = useState('all');
  const [filterIntent, setFilterIntent] = useState('all');

  // Selected Record detail
  const [selectedRecord, setSelectedRecord] = useState<CallRecord | null>(tenantRecords[0] || null);

  // Simulated Wave Audio Player
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);

  // QA Audit States for the current selected CDR
  const [qaIntents, setQaIntents] = useState<'high_interest' | 'mild_interest' | 'refusal' | 're_contact' | 'unknown'>('unknown');
  const [qaStatus, setQaStatus] = useState<'unreviewed' | 'passed' | 'rectified'>('unreviewed');
  const [qaComments, setQaComments] = useState('');

  // Update QA bindings when selectedRecord mutations occur
  useEffect(() => {
    if (selectedRecord) {
      setQaIntents(selectedRecord.intentTag);
      setQaStatus(selectedRecord.qaAuditStatus);
      setQaComments(selectedRecord.qaComments || '');
      setIsPlaying(false);
      setCurrentTime(0);
    }
  }, [selectedRecord]);

  // Audio timer ticker ticks every 1s
  useEffect(() => {
    let timer: any;
    if (isPlaying && selectedRecord) {
      timer = setInterval(() => {
        setCurrentTime(prev => {
          if (prev >= selectedRecord.durationSeconds) {
            setIsPlaying(false);
            return selectedRecord.durationSeconds;
          }
          return prev + 1;
        });
      }, 1000);
    }
    return () => clearInterval(timer);
  }, [isPlaying, selectedRecord]);

  // Handle saving the audit changes (RBAC qa:label)
  const handleSaveAudit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedRecord || !hasPermission('qa:label')) return;

    const updated: CallRecord = {
      ...selectedRecord,
      intentTag: qaIntents,
      qaAuditStatus: qaStatus,
      qaComments
    };

    onUpdateRecord(updated);
    setSelectedRecord(updated);
    onAddAuditLog('呼叫记录', '质检打标修改', `对号码 ${selectedRecord.customerPhone} 进行了人工话后质检纠偏。认定意向等级 [${qaIntents}], 结果：[${qaStatus}]`);

    // Nice success alert
    alert('✅ 质检标注打标结果已保存在案并实时入库！');
  };

  // Convert status flags to friendly strings
  const getStatusBadge = (status: CallRecord['status']) => {
    switch (status) {
      case 'connected': return <span className="bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-sm font-semibold">● 接通成功</span>;
      case 'unanswered': return <span className="bg-slate-100 text-slate-500 border border-slate-200 px-2 py-0.5 rounded-sm">○ 无人接听</span>;
      case 'rejected': return <span className="bg-amber-50 text-amber-700 border border-amber-200 px-2 py-0.5 rounded-sm">✖ 用户拒接</span>;
      case 'busy': return <span className="bg-blue-50 text-blue-700 border border-blue-200 px-2 py-0.5 rounded-sm">☏ 忙线占线</span>;
      default: return <span className="bg-rose-50 text-rose-700 border border-rose-200 px-2 py-0.5 rounded-sm">⚠️ 信号故障</span>;
    }
  };

  const getIntentLabel = (tag: CallRecord['intentTag']) => {
    switch (tag) {
      case 'high_interest': return <span className="bg-green-100 text-green-800 border border-green-200 px-2 rounded-full">高意向 / 承诺配合</span>;
      case 'mild_interest': return <span className="bg-blue-100 text-blue-800 border border-blue-200 px-2 rounded-full">中度意向 / 观望中</span>;
      case 'refusal': return <span className="bg-rose-100 text-rose-800 border border-rose-200 px-2 rounded-full">明确拒绝 / 投诉倾向</span>;
      case 're_contact': return <span className="bg-amber-100 text-amber-850 border border-amber-200 px-2 rounded-full">再行预约时间</span>;
      default: return <span className="bg-slate-100 text-slate-600 border border-slate-200 px-2 rounded-full">模糊未定</span>;
    }
  };

  // Filter conditions
  const filteredRecords = tenantRecords.filter(r => {
    const matchesPhone = r.customerPhone.includes(filterPhone) || r.customerName.includes(filterPhone);
    const matchesStatus = filterStatus === 'all' ? true : r.status === filterStatus;
    const matchesIntent = filterIntent === 'all' ? true : r.intentTag === filterIntent;
    return matchesPhone && matchesStatus && matchesIntent;
  });

  return (
    <div className="space-y-6">
      
      {/* Top Banner */}
      <div className="bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-800">呼叫记录与人工语音质检 (CDR & QA Inspector)</h2>
          <p className="text-xs text-slate-500 mt-1">调阅多租户流水话单，听取对象存储中留档的AI交互音频录音，评估对话意图及情绪并进行标注校准。</p>
        </div>

        {/* Export sheet (RBAC cdr:export) */}
        <button
          onClick={() => {
            if (hasPermission('cdr:export')) {
              onAddAuditLog('呼叫记录', '数据大表导出', '一键全盘导出了当前检索集下所有的外呼话单指标。');
              alert(`📥 租户隔离数据集已成功打标生成导出包：Galaxy_CDRs_${Date.now()}.csv`);
            }
          }}
          disabled={!hasPermission('cdr:export')}
          className={`flex items-center gap-1 px-4 py-2 text-xs font-semibold rounded-lg shrink-0 cursor-pointer ${
            hasPermission('cdr:export') ? 'bg-slate-900 border border-slate-800 text-white hover:bg-black' : 'bg-slate-50 text-slate-400 cursor-not-allowed'
          }`}
        >
          <FileDown className="w-3.5 h-3.5" /> 导出选定数据集 CSV
        </button>
      </div>

      {/* Grid structure: Left is CDR table list, Right is Wave Player, transcript & audit controls */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Column: List with search filters (Col span 5/12) */}
        <div className="lg:col-span-5 bg-white rounded-xl border border-slate-100 p-4 shadow-xs flex flex-col justify-between h-[640px]">
          <div className="space-y-3 flex-1 flex flex-col overflow-hidden">
            
            {/* Filters Toolbar */}
            <div className="space-y-2.5">
              <div className="relative">
                <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                <input
                  type="text"
                  placeholder="搜索客户姓名 / 拨号手机号..."
                  value={filterPhone}
                  onChange={e => setFilterPhone(e.target.value)}
                  className="w-full text-xs bg-slate-50 border border-slate-200 pl-9 py-2.5 rounded-lg focus:outline-none focus:border-indigo-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-2 text-xs">
                <select
                  value={filterStatus}
                  onChange={e => setFilterStatus(e.target.value)}
                  className="p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-500"
                >
                  <option value="all">📞 通话状态：不限</option>
                  <option value="connected">接通成功</option>
                  <option value="unanswered">无人接听</option>
                  <option value="rejected">用户拒接</option>
                  <option value="busy">忙线占线</option>
                </select>
                <select
                  value={filterIntent}
                  onChange={e => setFilterIntent(e.target.value)}
                  className="p-2 bg-slate-50 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-500"
                >
                  <option value="all">🎯 AI判定意向：不限</option>
                  <option value="high_interest">高意愿/承诺</option>
                  <option value="mild_interest">中度意向</option>
                  <option value="refusal">明确拒绝</option>
                  <option value="re_contact">另约时间</option>
                </select>
              </div>
            </div>

            {/* CDR Grid list scrollable */}
            <div className="flex-1 overflow-y-auto pr-1 space-y-2 mt-3 text-left">
              {filteredRecords.length === 0 ? (
                <p className="text-slate-400 text-xs text-center py-10 font-sans">暂无符合筛选条件的呼叫记录</p>
              ) : (
                filteredRecords.map(rec => {
                  const isActive = selectedRecord?.id === rec.id;
                  return (
                    <div
                      key={rec.id}
                      onClick={() => setSelectedRecord(rec)}
                      className={`p-3 rounded-lg border cursor-pointer transition-all ${
                        isActive ? 'border-indigo-600 bg-indigo-50/10 shadow-xs' : 'border-slate-150 hover:bg-slate-50'
                      }`}
                    >
                      <div className="flex justify-between items-center text-[10px]">
                        <span className="font-mono text-slate-400">{rec.createdAt}</span>
                        {getStatusBadge(rec.status)}
                      </div>

                      <div className="flex justify-between items-center mt-2.5">
                        <div>
                          <h4 className="text-xs font-bold text-slate-800">
                            {rec.customerName} <span className="font-mono text-slate-500 font-normal ml-1">({rec.customerPhone})</span>
                          </h4>
                          <p className="text-[10px] text-slate-500 truncate max-w-[200px] mt-0.5">{rec.taskName}</p>
                        </div>
                        <span className="text-xs font-mono font-bold text-slate-800">
                          {rec.durationSeconds} <span className="text-[10px] font-sans text-slate-400 font-normal">秒</span>
                        </span>
                      </div>

                      <div className="flex justify-between items-center mt-2 pt-2 border-t border-slate-100/50 text-[10px]">
                        <span className="text-slate-500 font-sans">AI资消耗: ¥{rec.aiSpentCost.toFixed(2)}</span>
                        {getIntentLabel(rec.intentTag)}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
            
          </div>
        </div>

        {/* Right Column: Recording, Transcript, Intent QA tag form (Col span 7/12) */}
        <div className="lg:col-span-7 bg-white rounded-xl border border-slate-100 p-5 shadow-xs flex flex-col min-h-[640px] text-left">
          {selectedRecord ? (
            <div className="flex-1 flex flex-col justify-between space-y-5">
              
              {/* Voice playback wave simulator (RBAC recording:play) */}
              <div className="bg-slate-50 p-4.5 rounded-xl border border-slate-150 space-y-3">
                <div className="flex justify-between items-center">
                  <span className="text-xs font-semibold text-slate-700 flex items-center gap-1">
                    <Volume2 className="w-4 h-4 text-indigo-500" />
                    在线音频试听录音 (Simulated Player)
                  </span>
                  
                  {/* Recording download action (RBAC recording:download) */}
                  <button
                    onClick={() => {
                      if (hasPermission('recording:download')) {
                        onAddAuditLog('呼叫记录', '下载录音音频', `下载了客户号码 ${selectedRecord.customerPhone} 规格为 MP3的外呼对话完备录音`);
                        alert('📥 音频文件已通过临时预签名URL下载至宿主终端！');
                      }
                    }}
                    disabled={!hasPermission('recording:download')}
                    className={`text-[10px] font-bold px-2 py-1 rounded border transition-all ${
                      hasPermission('recording:download') 
                        ? 'bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100' 
                        : 'bg-slate-100 text-slate-400 border-slate-200 cursor-not-allowed'
                    }`}
                  >
                    📥 原始高规格音频包下载
                  </button>
                </div>

                {hasPermission('recording:play') ? (
                  <div className="flex items-center gap-4">
                    {/* Play Button */}
                    <button
                      type="button"
                      onClick={() => setIsPlaying(!isPlaying)}
                      className="p-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-full transition-transform active:scale-95"
                    >
                      {isPlaying ? <Pause className="w-5 h-5 fill-white" /> : <Play className="w-5 h-5 fill-white ml-0.5" />}
                    </button>

                    {/* Faux Waveform columns */}
                    <div className="flex-1 space-y-1">
                      <div className="h-10 flex items-end gap-[2px]">
                        {[5, 14, 25, 38, 12, 18, 44, 16, 28, 6, 22, 38, 48, 10, 15, 34, 20, 8, 30, 42, 5, 12, 28, 45, 15, 6, 32, 14, 22, 38, 46, 50, 40, 12, 25, 33, 10, 5, 20, 30, 8].map((val, idx) => {
                          const currentPercent = selectedRecord.durationSeconds > 0 ? (currentTime / selectedRecord.durationSeconds) * 100 : 0;
                          const barIndexPercent = (idx / 41) * 100;
                          const played = barIndexPercent <= currentPercent;
                          return (
                            <div
                              key={idx}
                              style={{ height: `${val}%` }}
                              className={`flex-1 rounded-[1px] transition-colors ${played ? 'bg-indigo-600' : 'bg-slate-200'}`}
                            />
                          );
                        })}
                      </div>
                      <div className="flex justify-between items-center text-[10px] text-slate-400 font-mono">
                        <span>00:{String(currentTime).padStart(2, '0')}</span>
                        <span>00:{String(selectedRecord.durationSeconds).padStart(2, '0')}</span>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="p-4 bg-slate-100 text-slate-400 text-xs text-center flex items-center justify-center gap-1.5 rounded border border-slate-200 select-none">
                    <ShieldAlert className="w-4 h-4 text-slate-400" /> 只读观察组无法进行在线音频抓取与试听，请提权。
                  </div>
                )}
              </div>

              {/* transcripts chat list flows */}
              <div className="flex-1 overflow-y-auto space-y-3 max-h-[220px] bg-slate-50/50 rounded-xl border border-dashed border-slate-200 p-4.5">
                <span className="text-[10px] font-bold text-slate-400 block border-b border-slate-100 pb-1 mb-2 tracking-wide font-mono">AI RAG 话单转写交互过程细微追踪</span>

                {selectedRecord.messages.length === 0 ? (
                  <p className="text-slate-400 text-xs text-center py-10 font-sans">该通呼叫无应答，未产生任何对话转写数据流量。</p>
                ) : (
                  selectedRecord.messages.map((msg, i) => (
                    <div key={i} className={`flex gap-3 text-xs ${msg.sender === 'ai' ? 'justify-start' : 'justify-end'}`}>
                      {msg.sender === 'ai' && (
                        <div className="w-6 h-6 rounded-full bg-indigo-100 text-indigo-700 flex items-center justify-center font-bold text-[10px] shrink-0 font-sans">
                          AI
                        </div>
                      )}

                      <div className="space-y-1 max-w-[80%] text-left">
                        <div className={`p-2.5 rounded-xl border text-[11px] leading-relaxed font-mono ${
                          msg.sender === 'ai' 
                            ? 'bg-white text-slate-800 border-slate-150 rounded-tl-none' 
                            : 'bg-indigo-600 text-white border-indigo-500 rounded-tr-none'
                        }`}>
                          {msg.text}
                        </div>

                        {/* Sub metadata features from AI: intent keyword and sentiment */}
                        {msg.sender === 'customer' && (msg.intent || msg.sentiment) && (
                          <div className="flex gap-1.5 justify-end text-[9px]">
                            {msg.intent && (
                              <span className="bg-slate-100 border border-slate-250 text-slate-600 px-1 py-0.2 rounded font-semibold font-sans">
                                意图: {msg.intent}
                              </span>
                            )}
                            {msg.sentiment === 'negative' && (
                              <span className="bg-red-50 text-red-700 border border-red-200 px-1 rounded flex items-center gap-0.5"><AlertTriangle className="w-2.5 h-2.5" /> 负面焦灼</span>
                            )}
                            {msg.sentiment === 'positive' && (
                              <span className="bg-emerald-50 text-emerald-700 border border-emerald-200 px-1 rounded flex items-center gap-0.5"><Smile className="w-2.5 h-2.5" /> 态度正向</span>
                            )}
                          </div>
                        )}
                      </div>

                    </div>
                  ))
                )}
              </div>

              {/* Manual QA form (RBAC qa:label checking) - only show options if selected CDR has chat messages */}
              <div className="bg-slate-50 p-4 rounded-xl border border-slate-200">
                <form onSubmit={handleSaveAudit} className="space-y-3">
                  <div className="flex items-center gap-1 text-xs font-bold text-slate-800 border-b border-slate-200 pb-1.5 mb-2.5">
                    <ClipboardCheck className="w-4 h-4 text-emerald-500" />
                    <span>话务合规评判与标注工单表 (Manual Audit & calibration)</span>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                    <div className="space-y-1">
                      <label className="text-slate-500 font-semibold block">核实认定意向等级 (Intent)</label>
                      <select
                        value={qaIntents}
                        onChange={e => setQaIntents(e.target.value as any)}
                        disabled={!hasPermission('qa:label')}
                        className="w-full text-xs p-2 bg-white border border-slate-200 rounded focus:outline-none"
                      >
                        <option value="high_interest">承诺配合 / 高意向</option>
                        <option value="mild_interest">中度意向评估</option>
                        <option value="refusal">拒绝配合 / 催收失利</option>
                        <option value="re_contact">用户诉求预约再次拨打</option>
                        <option value="unknown">模糊未确定</option>
                      </select>
                    </div>

                    <div className="space-y-1">
                      <label className="text-slate-500 font-semibold block">人工质检审查结论 (QA Status)</label>
                      <select
                        value={qaStatus}
                        onChange={e => setQaStatus(e.target.value as any)}
                        disabled={!hasPermission('qa:label')}
                        className="w-full text-xs p-2 bg-white border border-slate-200 rounded focus:outline-none"
                      >
                        <option value="unreviewed">尚未人工抽检</option>
                        <option value="passed">🟢 质检达标通过</option>
                        <option value="rectified">🔴 需整改话务话合规告警</option>
                      </select>
                    </div>
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold block">质检说明与专家备注</label>
                    <textarea
                      value={qaComments}
                      onChange={e => setQaComments(e.target.value)}
                      placeholder="写下关于本通通话审核结论。例：AI应答精准命中，但客户态度焦躁可能引发越级保监会投诉..."
                      disabled={!hasPermission('qa:label')}
                      rows={2.5}
                      className="w-full text-xs p-2 bg-white border border-slate-200 rounded focus:outline-none resize-none font-mono"
                    />
                  </div>

                  <div className="flex justify-end pt-1">
                    <button
                      type="submit"
                      disabled={!hasPermission('qa:label')}
                      className={`text-xs px-4 py-2 font-bold rounded-lg cursor-pointer transition-colors ${
                        hasPermission('qa:label') 
                          ? 'bg-indigo-600 hover:bg-indigo-700 text-white' 
                          : 'bg-slate-200 text-slate-400 cursor-not-allowed'
                      }`}
                    >
                      提交质检标定结果入库
                    </button>
                  </div>
                </form>
              </div>

            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center text-center p-6 text-slate-400 space-y-2 select-none h-full">
              <ClipboardCheck className="w-12 h-12 stroke-1" />
              <div>
                <h4 className="text-slate-700 font-semibold text-xs">无选定通话话单记录</h4>
                <p className="text-[11px] text-slate-400">请在左列挑选任何一通通话细目，加载动态波形图与情感语谱记录。</p>
              </div>
            </div>
          )}

        </div>

      </div>
    </div>
  );
}
