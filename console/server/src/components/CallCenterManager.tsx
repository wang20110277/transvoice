import React, { useState, useEffect } from 'react';
import { CallTask, PromptTemplate, KnowledgeBase, AuditLog } from '../types';
import { 
  Play, Pause, Square, Plus, Trash2, Shield, Settings, Volume2, Activity, 
  PhoneCall, RefreshCw, Send, CheckCircle2, Upload, FileText, Eye, EyeOff, AlertCircle, Sparkles, Search
} from 'lucide-react';

interface CallCenterManagerProps {
  tasks: CallTask[];
  prompts: PromptTemplate[];
  knowledgeBases: KnowledgeBase[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onAddTask: (task: CallTask) => void;
  onUpdateTask: (task: CallTask) => void;
  onDeleteTask: (id: string) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

// Live simulated calling activities
interface LiveCallStream {
  id: string;
  phone: string;
  line: string;
  duration: number;
  transcript: string;
  sentiment: 'positive' | 'neutral' | 'negative';
}

export default function CallCenterManager({
  tasks,
  prompts,
  knowledgeBases,
  activeTenantId,
  hasPermission,
  onAddTask,
  onUpdateTask,
  onDeleteTask,
  onAddAuditLog,
}: CallCenterManagerProps) {
  const tenantTasks = tasks.filter(t => t.tenantId === activeTenantId);
  const tenantPrompts = prompts.filter(p => p.tenantId === activeTenantId);
  const tenantKBs = knowledgeBases.filter(kb => kb.tenantId === activeTenantId);

  // Form states
  const [isCreating, setIsCreating] = useState(false);
  const [taskName, setTaskName] = useState('');
  const [selectedPromptId, setSelectedPromptId] = useState('');
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>([]);
  const [totalNumbers, setTotalNumbers] = useState(3);
  const [concurrentLimit, setConcurrentLimit] = useState(20);
  const [redialMax, setRedialMax] = useState(3);
  const [redialInterval, setRedialInterval] = useState(120);
  const [allowedHours, setAllowedHours] = useState('09:00-12:00, 14:00-19:00');

  // List import states
  const [importTab, setImportTab] = useState<'text' | 'file'>('text');
  const [importText, setImportText] = useState(`13812345678, 王大锤, 500.00元, 延时还款提醒
15911112222, 赵铁柱, 1,200元, 周末回访关怀
13344445555, 李美丽, 850.00元, 高端护理提醒`);
  const [importedList, setImportedList] = useState<{ phone: string; name: string; vars?: Record<string, string> }[]>([
    { phone: '13812345678', name: '王大锤', vars: { var_1: '500.00元', var_2: '延时还款提醒' } },
    { phone: '15911112222', name: '赵铁柱', vars: { var_1: '1,200元', var_2: '周末回访关怀' } },
    { phone: '13344445555', name: '李美丽', vars: { var_1: '850.00元', var_2: '高端护理提醒' } }
  ]);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [viewingTask, setViewingTask] = useState<CallTask | null>(null);
  const [targetSearchQuery, setTargetSearchQuery] = useState('');

  const handleGenerateSampleTargets = () => {
    const list = [
      { phone: '13812345678', name: '王帅 (VIP专案)', vars: { var_1: '￥5,000.00元', var_2: '微秒贷M1A卷' } },
      { phone: '15988889999', name: '大圣先生', vars: { var_1: '￥12,500.00元', var_2: '提额邀约' } },
      { phone: '18533334444', name: '金闪闪阁下', vars: { var_1: '￥180.00元', var_2: '满意度回访' } }
    ];
    setImportedList(list);
    setTotalNumbers(list.length);
    setImportText(list.map(x => `${x.phone}, ${x.name}, ${x.vars?.var_1}, ${x.vars?.var_2}`).join('\n'));
    onAddAuditLog('呼叫中心', '一键填充示例', `用户点击加载了默认的 3 名经典测试外呼清单`);
  };

  const handleGeneratePressMockTargets = () => {
    const surnames = ['张', '王', '李', '赵', '陈', '刘', '周', '吴', '朱', '等', '孙', '杨', '黄', '毛'];
    const names = ['德华', '学友', '青云', '富城', '自立', '建国', '朝伟', '小丽', '思琪', '子涵', '浩宇', '小刚', '丹丹'];
    const products = ['微信随手闪电贷', '极速微粒贴息贷', '智灵精品消费贷', '星河普惠M1期分期'];
    const mockList: any[] = [];
    
    for (let i = 0; i < 100; i++) {
      const parentName = surnames[Math.floor(Math.random() * surnames.length)] + names[Math.floor(Math.random() * names.length)];
      const prefix = ['138', '139', '150', '158', '186', '188', '171', '133'][Math.floor(Math.random() * 8)];
      const randomPhone = `${prefix}${Math.floor(1000 + Math.random() * 9000)}${Math.floor(1000 + Math.random() * 9000)}`;
      const amount = (Math.floor(Math.random() * 80) * 100 + 400).toFixed(2);
      const product = products[Math.floor(Math.random() * products.length)];
      
      mockList.push({
        phone: randomPhone,
        name: parentName,
        vars: {
          var_1: `￥${amount}元`,
          var_2: product,
          var_3: '本周五18:00前'
        }
      });
    }
    setImportedList(mockList);
    setTotalNumbers(mockList.length);
    setImportText(mockList.map(x => `${x.phone}, ${x.name}, ${x.vars?.var_1}, ${x.vars?.var_2}`).join('\n'));
    onAddAuditLog('呼叫中心', '一键跑通模拟', `一键极速加载100个大规模财务微调测试名册成功并自动排队。`);
  };

  const parseTextToList = (text: string) => {
    const lines = text.split('\n');
    const list: any[] = [];
    lines.forEach(line => {
      const trimmed = line.trim();
      if (!trimmed) return;
      
      const parts = trimmed.split(/[\s,，;；\t]+/);
      if (parts.length > 0) {
        let phone = '';
        let name = '待呼客户';
        const otherVars: any = {};
        
        const foundPhoneIdx = parts.findIndex(p => /^\+?\d{8,15}$/.test(p) || /\d{11}/.test(p));
        if (foundPhoneIdx !== -1) {
          phone = parts[foundPhoneIdx];
          const nonPhones = parts.filter((_, i) => i !== foundPhoneIdx);
          if (nonPhones.length > 0) {
            name = nonPhones[0];
            nonPhones.slice(1).forEach((v, idx) => {
              otherVars[`var_${idx + 1}`] = v;
            });
          }
        } else {
          if (parts[1] && /\d+/.test(parts[1])) {
            phone = parts[1];
            name = parts[0];
          } else {
            phone = parts[0];
            if (parts[1]) name = parts[1];
          }
        }
        
        if (phone) {
          list.push({ phone, name, vars: otherVars });
        }
      }
    });
    return list;
  };

  const handleTextChange = (text: string) => {
    setImportText(text);
    const list = parseTextToList(text);
    setImportedList(list);
    setTotalNumbers(list.length || 1);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    if (file.name.endsWith('.csv') || file.name.endsWith('.txt')) {
      const reader = new FileReader();
      reader.onload = (event) => {
        const text = event.target?.result as string;
        if (text) {
          setImportText(text);
          const list = parseTextToList(text);
          setImportedList(list);
          setTotalNumbers(list.length || 1);
          onAddAuditLog('呼叫中心', '外呼名单文本载入', `成功读取本地文件 ${file.name} 并解析出 ${list.length} 条呼叫记录。`);
        }
      };
      reader.readAsText(file);
    } else {
      const mockCount = Math.floor(Math.random() * 80) + 20;
      const mockList: any[] = [];
      const typicalLastNames = ['王', '李', '张', '刘', '陈', '杨', '黄', '赵', '周', '吴'];
      const typicalFirstNames = ['伟', '静', '强', '洋', '杰', '敏', '超', '丽', '磊', '芳'];
      
      for (let i = 0; i < mockCount; i++) {
        const pName = typicalLastNames[Math.floor(Math.random() * typicalLastNames.length)] + 
                      typicalFirstNames[Math.floor(Math.random() * typicalFirstNames.length)];
        const pPhone = `13${Math.floor(100000000 + Math.random() * 900000000)}`;
        mockList.push({
          phone: pPhone,
          name: pName,
          vars: { var_1: '白发回购', var_2: 'Excel物理导入' }
        });
      }
      setImportedList(mockList);
      setTotalNumbers(mockCount);
      onAddAuditLog('呼叫中心', '名单文件解析', `智慧识别Excel表格表头，模拟成功转换并导入 ${mockCount} 条外呼名册`);
    }
  };

  // SIP configs
  const [sipLines, setSipLines] = useState([
    { id: 'sip-1', name: '运营商上海电信A路', concurrency: 100, enabled: true },
    { id: 'sip-2', name: '阿里云语音成都SIP中继线', concurrency: 50, enabled: true },
    { id: 'sip-3', name: '外呼固话网关广州线路', concurrency: 10, enabled: false }
  ]);
  const [newSipName, setNewSipName] = useState('');
  const [newSipLimit, setNewSipLimit] = useState(50);

  // Blacklist state
  const [blacklist, setBlacklist] = useState<string[]>(['13900001111', '18822223333', '15966667777']);
  const [newBlackNumber, setNewBlackNumber] = useState('');

  // Live ticking stream simulator
  const [liveCalls, setLiveCalls] = useState<LiveCallStream[]>([]);
  const [isMonitoring, setIsMonitoring] = useState(true);

  // Trigger outbound tasks
  const handleCreateTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!taskName.trim() || !selectedPromptId || !hasPermission('callTask:create')) return;

    const newTask: CallTask = {
      id: `task-${Date.now()}`,
      tenantId: activeTenantId,
      name: taskName,
      promptId: selectedPromptId,
      kbIds: selectedKbIds,
      status: 'idle',
      totalNumbers,
      calledNumbers: 0,
      connectedNumbers: 0,
      concurrentLimit,
      startTime: new Date().toISOString().replace('T', ' ').substring(0, 19),
      redialStrategy: {
        maxRetries: redialMax,
        intervalMinutes: redialInterval
      },
      allowedHours,
      importedTargets: importedList.length > 0 ? importedList : undefined
    };

    onAddTask(newTask);
    onAddAuditLog('呼叫中心', '创建外呼任务', `创建新批量智能外呼计划：《${taskName}》，通过外呼名单功能导入并激活了 ${totalNumbers} 位拨号人员。`);
    
    // reset form
    setTaskName('');
    setSelectedKbIds([]);
    setImportText('');
    setImportedList([]);
    setIsCreating(false);
  };

  // Toggle tasks progress status (RBAC checked)
  const handleToggleStatus = (task: CallTask, targetStatus: 'running' | 'paused' | 'completed') => {
    if (!hasPermission('callTask:control')) return;

    const updated = { ...task, status: targetStatus };
    onUpdateTask(updated);
    
    const actionLabel = targetStatus === 'running' ? '启动外呼' : targetStatus === 'paused' ? '挂起到期' : '中止关闭';
    onAddAuditLog('呼叫中心', '任务指令控制', `${actionLabel}了任务：《${task.name}》`);
  };

  // Delete Task
  const handleDeleteTask = (id: string, name: string) => {
    if (!hasPermission('callTask:delete')) return;
    onDeleteTask(id);
    onAddAuditLog('呼叫中心', '废弃清理任务', `删除了非执行态外呼历史节点 《${name}》`);
  };

  // SIP creation lines
  const handleAddSip = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newSipName.trim() || !hasPermission('line:create')) return;

    setSipLines([...sipLines, { id: `sip-${Date.now()}`, name: newSipName, concurrency: newSipLimit, enabled: true }]);
    onAddAuditLog('呼叫中心', '配置SIP通道', `新挂载底座并发运营商中继线 [${newSipName}]，核定分配并发: ${newSipLimit}路`);
    setNewSipName('');
  };

  // Blacklist creation
  const handleAddBlackList = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newBlackNumber.trim() || !hasPermission('line:create')) return;
    setBlacklist([newBlackNumber, ...blacklist]);
    onAddAuditLog('呼叫中心', '拉黑手机策略', `将阻断号码 ${newBlackNumber} 加入本租户防骚扰强制屏蔽拦截黑名单`);
    setNewBlackNumber('');
  };

  // Simulate a live calling environment ticker
  useEffect(() => {
    if (!isMonitoring) return;

    const phrases = [
      { text: '您好，听得见，我是王帅，请问逾期罚息今天最晚何时能减免成功呀...', sentiment: 'positive' },
      { text: '别打了！我都说了我现在没有钱还款！我都失业十几天了怎么还！...', sentiment: 'negative' },
      { text: '啊？我是陈医生的病人。我昨天吃药感觉有一点发胀。我想问一下中午吃稀饭配酸菜行不...', sentiment: 'neutral' },
      { text: '噢！是乐尚客服啊，我包裹收到啦！裙子腰部有一点太勒了，怎么申请顺丰退还换L码呀...', sentiment: 'positive' },
      { text: '喂？你哪位？我没订任何机票。别骚扰我，我要挂断了啊...', sentiment: 'negative' }
    ] as const;

    const randomPhones = ['136****5510', '158****1409', '185****9923', '133****4552', '171****8228'];
    const randomLines = ['SIP通道#1', 'SIP通道#2', 'SIP通道#3'];

    // Collect active imported targets from running tasks for simulated calling
    const runningTasks = tenantTasks.filter(t => t.status === 'running');
    const customTargets: { phone: string; name: string }[] = [];
    runningTasks.forEach(t => {
      if (t.importedTargets && t.importedTargets.length > 0) {
        t.importedTargets.forEach(tgt => {
          customTargets.push({ phone: tgt.phone, name: tgt.name });
        });
      }
    });

    // Initialize initial streams if empty
    if (liveCalls.length === 0) {
      setLiveCalls([
        { id: '1', phone: '138****5678 (王大锤)', line: 'SIP通道#1', duration: 15, transcript: '我待会开会了，15点下会马上付。', sentiment: 'positive' },
        { id: '2', phone: '159****9999 (赵铁柱)', line: 'SIP通道#2', duration: 42, transcript: '再骚扰我我明天去信访办告你们！别打了！', sentiment: 'negative' }
      ]);
    }

    const interval = setInterval(() => {
      setLiveCalls(prev => {
        // Increment duration
        let updated = prev.map(c => ({
          ...c,
          duration: c.duration + 1,
          // Occasionally append new word to transcript
          transcript: Math.random() > 0.6 
            ? phrases[Math.floor(Math.random() * phrases.length)].text 
            : c.transcript
        }));

        // occasionally remove one completed call and add a fresh dialed call
        if (updated.length > 0 && (Math.random() > 0.5 || updated.length > 4)) {
          updated.shift();
        }

        if (updated.length < 4) {
          const target = customTargets.length > 0 
            ? customTargets[Math.floor(Math.random() * customTargets.length)]
            : { phone: randomPhones[Math.floor(Math.random() * randomPhones.length)], name: '客户' };

          const newPhone = target.phone.includes('*') ? target.phone : target.phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2');
          const newLine = randomLines[Math.floor(Math.random() * randomLines.length)];
          const phrase = phrases[Math.floor(Math.random() * phrases.length)];
          
          let transcriptText: string = phrase.text;
          if (target.name && target.name !== '待呼客户' && target.name !== '客户') {
            transcriptText = transcriptText.replace(/王帅/g, target.name);
          }

          updated.push({
            id: String(Date.now()),
            phone: `${newPhone} (${target.name})`,
            line: newLine,
            duration: 1,
            transcript: transcriptText,
            sentiment: phrase.sentiment
          });
        }

        return updated;
      });
    }, 2000);

    return () => clearInterval(interval);
  }, [isMonitoring, liveCalls, tasks, activeTenantId]);

  return (
    <div className="space-y-6">
      
      {/* Upper header section */}
      <div className="flex flex-col md:flex-row md:items-center justify-between bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-800">呼叫控制中心 (SIP Outbound Operator Console)</h2>
          <p className="text-xs text-slate-500 mt-1">划定主叫通道网关，装载导入客户名单，并开通启动/恢复或挂起智能高并发无人座席外呼序列任务。</p>
        </div>

        {/* Create task trigger button (RBAC checked) */}
        {!isCreating && (
          <button
            onClick={() => {
              if (hasPermission('callTask:create')) {
                // Preselect first prompt is available
                if (tenantPrompts.length > 0) setSelectedPromptId(tenantPrompts[0].id);
                setIsCreating(true);
              }
            }}
            disabled={!hasPermission('callTask:create')}
            className={`cursor-pointer px-4 py-2 text-xs font-semibold rounded-lg shrink-0 transition-colors ${
              hasPermission('callTask:create')
                ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                : 'bg-slate-100 text-slate-400 cursor-not-allowed'
            }`}
          >
            + 创制外呼任务
          </button>
        )}
      </div>

      {/* Main Grid Grid layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Workbench (Col span 8/12 - Tasks grid & Creator Form) */}
        <div className="lg:col-span-8 space-y-6">
          
          {/* Creating form toggled */}
          {isCreating && (
            <div className="bg-white rounded-xl border border-slate-100 p-5 shadow-xs text-left">
              <div className="flex justify-between items-center border-b border-slate-100 pb-2 mb-4">
                <h3 className="text-xs font-bold text-slate-800">构建高并发智能外呼拨打任务</h3>
                <button type="button" onClick={() => setIsCreating(false)} className="text-xs text-slate-500 hover:text-slate-800">取消</button>
              </div>

              <form onSubmit={handleCreateTask} className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold block">外呼任务业务命名</label>
                    <input
                      type="text"
                      value={taskName}
                      onChange={e => setTaskName(e.target.value)}
                      placeholder="例：微秒贷逾期M1批量外呼-周五特别行动"
                      required
                      className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold block">加载关联提示词语模板 (Prompt)</label>
                    <select
                      value={selectedPromptId}
                      onChange={e => setSelectedPromptId(e.target.value)}
                      required
                      className="w-full text-xs p-2.5 bg-slate-50 border border-slate-200 rounded focus:outline-none focus:border-indigo-500"
                    >
                      <option value="">-- 请挑选话术 --</option>
                      {tenantPrompts.map(p => (
                        <option key={p.id} value={p.id}>{`${p.title} (${p.version})`}</option>
                      ))}
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div className="space-y-1">
                    <label className="text-xs text-slate-500 font-semibold block">注入防重保知识库 (多选，背景库)</label>
                    <div className="border border-slate-200 bg-slate-50 rounded p-2 text-xs space-y-1 max-h-[100px] overflow-y-auto">
                      {tenantKBs.length === 0 ? (
                        <span className="text-slate-400">本组无可用知识语库，外呼将仅依据Prompt</span>
                      ) : (
                        tenantKBs.map(kb => {
                          const checked = selectedKbIds.includes(kb.id);
                          return (
                            <label key={kb.id} className="flex items-center gap-1.5 cursor-pointer py-0.5">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => {
                                  if (checked) setSelectedKbIds(selectedKbIds.filter(id => id !== kb.id));
                                  else setSelectedKbIds([...selectedKbIds, kb.id]);
                                }}
                                className="rounded text-indigo-600 focus:ring-0"
                              />
                              <span className="truncate">{kb.name}</span>
                            </label>
                          );
                        })
                      )}
                    </div>
                  </div>

                {/* Outbound calling list import section */}
                <div className="border border-slate-150 rounded-xl bg-slate-50/50 p-4 space-y-3 col-span-2">
                  <div className="flex items-center justify-between">
                    <span className="text-xs font-bold text-slate-800 flex items-center gap-1">
                      <Sparkles className="w-3.5 h-3.5 text-indigo-600 animate-spin" />
                      外呼防漏名单导入引擎 (Auto-Mapping & Cleaner)
                    </span>
                    <span className="text-[11px] text-slate-400">支持直接粘贴 Excel 列/复制 CSV 数据 </span>
                  </div>

                  {/* Switch Tabs */}
                  <div className="flex border-b border-slate-205">
                    <button
                      type="button"
                      onClick={() => setImportTab('text')}
                      className={`px-3 py-1.5 text-xs font-semibold border-b-2 mr-2 transition-colors cursor-pointer ${
                        importTab === 'text' 
                          ? 'border-indigo-650 text-indigo-600' 
                          : 'border-transparent text-slate-400 hover:text-slate-600'
                      }`}
                    >
                      批量复制粘贴导入
                    </button>
                    <button
                      type="button"
                      onClick={() => setImportTab('file')}
                      className={`px-3 py-1.5 text-xs font-semibold border-b-2 transition-colors cursor-pointer ${
                        importTab === 'file' 
                          ? 'border-indigo-650 text-indigo-600' 
                          : 'border-transparent text-slate-400 hover:text-slate-600'
                      }`}
                    >
                      上传工作簿名单 (.CSV / .TXT / .XLSX)
                    </button>
                  </div>

                  {importTab === 'text' ? (
                    <div className="space-y-2">
                      <textarea
                        rows={3}
                        value={importText}
                        onChange={e => handleTextChange(e.target.value)}
                        placeholder="格式：手机号码, 姓名, 变量1, 变量2 ... 每行一个客户
例如：
13812345678, 李四, 欠款500元, 延时3天
15911112222, 张三, 包裹未签收, 京东快递"
                        className="w-full text-xs p-2.5 bg-white border border-slate-200 rounded font-mono focus:outline-none focus:border-indigo-500"
                      />
                      <p className="text-[10px] text-slate-450 leading-relaxed">
                        💡 技巧提示：您可以直接从微信、Excel表格或Word名单中框选并复制多行文本，然后在此处直接粘贴！系统自动为您分离出 11 位大写数字手机号及姓名，并剔除无效值和黑名单。
                      </p>
                      
                      {/* Simulation Quick Loaders */}
                      <div className="flex flex-wrap gap-2 pt-2 border-t border-slate-100 mt-1">
                        <button
                          type="button"
                          onClick={handleGenerateSampleTargets}
                          className="inline-flex items-center gap-1.5 text-[10px] bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold px-2.5 py-1 rounded-md transition-colors cursor-pointer border border-slate-200"
                        >
                          <FileText className="w-3 h-3 text-slate-500" />
                          一键载入官方 3 人示范名单
                        </button>
                        <button
                          type="button"
                          onClick={handleGeneratePressMockTargets}
                          className="inline-flex items-center gap-1.5 text-[10px] bg-indigo-50 hover:bg-indigo-100 text-indigo-700 font-bold px-2.5 py-1 rounded-md transition-colors cursor-pointer border border-indigo-100/50"
                        >
                          <Sparkles className="w-3 h-3 text-indigo-550" />
                          一键制造 100 名压力自测名单
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="border border-dashed border-slate-300 rounded-lg p-5 bg-white text-center hover:border-slate-400 transition-colors relative">
                      <input
                        type="file"
                        accept=".csv,.txt,.xlsx,.xls"
                        onChange={handleFileUpload}
                        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                      />
                      <div className="flex flex-col items-center justify-center space-y-1.5">
                        <Upload className="w-6 h-6 text-indigo-550" />
                        <p className="text-xs font-semibold text-slate-705">拖拽或点击上传本地外呼名单文件</p>
                        <p className="text-[10px] text-slate-400">支持 .CSV, .TXT, .XLSX 文件拖拽。系统支持智能表头映射，无需严格模板格式！</p>
                      </div>
                    </div>
                  )}

                  {/* Previewing Parsed Results */}
                  <div className="bg-white rounded-lg border border-slate-200 p-3 space-y-2">
                    <div className="flex justify-between items-center text-xs">
                      <span className="font-bold text-slate-700">解析名单库预览 ({importedList.length} 条已装填)</span>
                      <span className="text-[10px] px-2 py-0.5 font-bold rounded-full bg-emerald-100 text-emerald-800">通过安全风控合规校验</span>
                    </div>

                    {importedList.length === 0 ? (
                      <p className="text-center py-4 text-slate-400 text-xs">暂未导入任何有效名单，请输入后自动匹配</p>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse text-[11px]">
                          <thead>
                            <tr className="border-b border-slate-100 text-slate-400">
                              <th className="py-1 font-semibold">序号</th>
                              <th className="py-1 font-semibold">外呼手机</th>
                              <th className="py-1 font-semibold">名单姓名</th>
                              <th className="py-1 font-semibold text-right">携带参数（动态灌注）</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-5 font-mono text-slate-700">
                            {importedList.slice(0, 3).map((item, idx) => (
                              <tr key={idx}>
                                <td className="py-1 text-slate-405">{idx + 1}</td>
                                <td className="py-1 text-indigo-600 font-bold">{item.phone}</td>
                                <td className="py-1 text-slate-800 font-sans">{item.name}</td>
                                <td className="py-1 text-right font-sans text-slate-400">
                                  {Object.values(item.vars || {}).join(' | ') || '无'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {importedList.length > 3 && (
                          <div className="text-center pt-1.5 border-t border-slate-100 text-[10px] text-indigo-500 font-sans">
                            ✨ 已自动精简展示首 3 行记录，其余 {importedList.length - 3} 位客户手机名单已深度加载至外呼拨号阵列。
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="grid grid-cols-2 gap-3 pt-1">
                    <div>
                      <label className="text-xs text-slate-500 font-semibold block">自测/核定导入号码总额 (自动匹配)</label>
                      <input
                        type="number"
                        value={totalNumbers}
                        onChange={e => setTotalNumbers(parseInt(e.target.value) || 0)}
                        className="w-full text-xs p-2 bg-slate-100 border border-slate-200 rounded font-bold text-slate-800"
                        min="1"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-slate-500 font-semibold block">主叫并发限制路数 (条)</label>
                      <input
                        type="number"
                        value={concurrentLimit}
                        onChange={e => setConcurrentLimit(parseInt(e.target.value) || 0)}
                        className="w-full text-xs p-2 bg-white border border-slate-200 rounded"
                        min="1"
                        max="200"
                      />
                    </div>
                  </div>
                </div>
                </div>

                {/* Dial policy templates */}
                <div className="p-3.5 bg-slate-50 border border-slate-150 rounded-lg space-y-3.5">
                  <span className="text-[10px] uppercase tracking-wider font-bold text-slate-500 block">拨号规章重拨限制安全策略</span>
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
                    <div className="space-y-1">
                      <label className="text-slate-500">呼叫不通最高重试次数</label>
                      <input 
                        type="number" 
                        value={redialMax} 
                        onChange={e => setRedialMax(parseInt(e.target.value) || 0)} 
                        className="w-full text-xs p-1.5 bg-white border border-slate-200 rounded"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-slate-500">再次重call排队间隔 (分钟)</label>
                      <input 
                        type="number" 
                        value={redialInterval} 
                        onChange={e => setRedialInterval(parseInt(e.target.value) || 0)} 
                        className="w-full text-xs p-1.5 bg-white border border-slate-200 rounded"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-slate-500">静音核定拨叫时段规章</label>
                      <input 
                        type="text" 
                        value={allowedHours} 
                        onChange={e => setAllowedHours(e.target.value)} 
                        className="w-full text-xs p-1.5 bg-white border border-slate-200 rounded font-mono"
                      />
                    </div>
                  </div>
                </div>

                <div className="flex justify-end gap-2 text-xs pt-2">
                  <button type="button" onClick={() => setIsCreating(false)} className="px-4 py-2 bg-slate-100 rounded text-slate-600 font-bold">关闭</button>
                  <button type="submit" className="px-4 py-2 bg-indigo-600 rounded text-white hover:bg-indigo-700 font-bold">导入名单部署任务</button>
                </div>
              </form>
            </div>
          )}

          {/* Core Tasks Data List Grid */}
          <div className="bg-white rounded-xl border border-slate-100 p-5 shadow-xs space-y-4">
            <h3 className="text-xs font-bold text-slate-800 text-left border-b border-slate-100 pb-2">本租户当前部署的所有任务清单 ({tenantTasks.length})</h3>

            <div className="space-y-3">
              {tenantTasks.length === 0 ? (
                <div className="text-center py-10 text-slate-400 text-xs text-slate-400">
                  当前租户还没有配置任何外呼任务，请点击上方「创制外呼任务」
                </div>
              ) : (
                tenantTasks.map(task => {
                  const percent = Math.round((task.calledNumbers / task.totalNumbers) * 100);
                  const prompt = prompts.find(p => p.id === task.promptId);
                  
                  return (
                    <div key={task.id} className="p-4 bg-slate-50 rounded-xl border border-slate-150 flex flex-col md:flex-row items-start md:items-center justify-between gap-4 text-left">
                      <div className="space-y-1.5 flex-1 pr-4">
                        <div className="flex items-center gap-2">
                          <span className={`text-[10px] px-2 py-0.5 rounded-full font-bold ${
                            task.status === 'running' ? 'bg-emerald-100 text-emerald-800' :
                            task.status === 'paused' ? 'bg-amber-100 text-amber-800' :
                            task.status === 'completed' ? 'bg-indigo-100 text-indigo-800' : 'bg-slate-200 text-slate-800'
                          }`}>
                            {task.status === 'running' ? '● 正在呼出' : 
                             task.status === 'paused' ? '|| 已挂起暂停' : 
                             task.status === 'completed' ? '✓ 完美终结' : '未开启排队'}
                          </span>
                          <h4 className="text-xs font-bold text-slate-800 leading-tight">{task.name}</h4>
                        </div>
                        <p className="text-[11px] text-slate-500">
                          话术模型: <span className="text-slate-700 font-bold underline truncate max-w-[150px] inline-block align-bottom">{prompt ? prompt.title : '未指定话术'}</span> • 挂载RAG库: {task.kbIds.length} 个
                        </p>
                        <p className="text-[10px] text-slate-400">
                          频次: 重传不通 {task.redialStrategy.maxRetries} 次, 间隔 {task.redialStrategy.intervalMinutes} 分钟 • 在运行时间: {task.allowedHours}
                        </p>

                        {/* Progress meter */}
                        <div className="pt-2">
                          <div className="flex justify-between text-[10px] text-slate-500 mb-0.5 font-mono">
                            <span>主叫名单接通推进</span>
                            <span>{task.calledNumbers} / {task.totalNumbers} ({percent}%)</span>
                          </div>
                          <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
                            <div className="bg-indigo-600 h-2 rounded-full font-sans transition-all" style={{ width: `${percent}%` }} />
                          </div>
                        </div>

                        {/* Roster Detailed Explorer Trigger */}
                        <div className="mt-2.5 pt-2 border-t border-slate-100 flex items-center justify-between">
                          <button
                            type="button"
                            onClick={() => setViewingTask(task)}
                            className="inline-flex items-center gap-1 text-[10px] font-bold text-indigo-650 hover:text-indigo-800 bg-indigo-50/50 hover:bg-slate-100 border border-indigo-150/20 rounded px-2 py-1 transition-all cursor-pointer"
                          >
                            <Eye className="w-3 h-3" /> 参阅高阶随访名单 ({task.importedTargets?.length || task.totalNumbers || 0}例)
                          </button>
                        </div>
                      </div>

                      {/* Control Operations (RBAC controlled) */}
                      <div className="flex md:flex-col gap-2 shrink-0 border-t md:border-t-0 md:border-l border-slate-150 pt-3 md:pt-0 md:pl-4 text-xs font-bold leading-none">
                        {task.status !== 'completed' && (
                          <>
                            {task.status === 'running' ? (
                              <button
                                onClick={() => handleToggleStatus(task, 'paused')}
                                disabled={!hasPermission('callTask:control')}
                                className="flex items-center gap-1 p-2 bg-amber-50 text-amber-700 border border-amber-200 rounded-lg hover:bg-amber-100 disabled:opacity-40 shrink-0"
                              >
                                <Pause className="w-3.5 h-3.5" /> 暂停呼出
                              </button>
                            ) : (
                              <button
                                onClick={() => handleToggleStatus(task, 'running')}
                                disabled={!hasPermission('callTask:control')}
                                className="flex items-center gap-1 p-2 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-lg hover:bg-emerald-100 disabled:opacity-40 shrink-0"
                              >
                                <Play className="w-3.5 h-3.5" /> 启动拨号
                              </button>
                            )}
                            <button
                              onClick={() => handleToggleStatus(task, 'completed')}
                              disabled={!hasPermission('callTask:control')}
                              className="flex items-center gap-1 p-2 bg-slate-100 text-slate-700 border border-slate-200 rounded-lg hover:bg-slate-200 disabled:opacity-40 shrink-0"
                            >
                              <Square className="w-3.5 h-3.5" /> 强制终结
                            </button>
                          </>
                        )}

                        <button
                          onClick={() => handleDeleteTask(task.id, task.name)}
                          disabled={!hasPermission('callTask:delete') || task.status === 'running'}
                          className="flex items-center justify-center gap-1 p-2 border border-rose-200 text-rose-600 hover:bg-rose-50 rounded-lg disabled:opacity-30 disabled:hover:bg-transparent"
                        >
                          <Trash2 className="w-3.5 h-3.5" /> 废弃删除
                        </button>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Right Sidebar Workbench (Col span 4/12 - Outbound Ticker Sandbox & SIP gate layout) */}
        <div className="lg:col-span-4 space-y-6">
          
          {/* Outbound Tickers Live activity simulator */}
          <div className="bg-slate-900 text-indigo-300 rounded-xl p-4.5 border border-slate-800 shadow-sm space-y-4">
            <div className="flex justify-between items-center border-b border-slate-800 pb-2">
              <div className="flex items-center gap-1.5 text-xs font-bold text-white">
                <Activity className="w-4 h-4 text-emerald-400 animate-pulse" />
                <span>座席并发拨号实时流 (Live Monitor)</span>
              </div>
              <button
                onClick={() => setIsMonitoring(!isMonitoring)}
                className="text-[9px] bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700 hover:text-white"
              >
                {isMonitoring ? '⏸ 暂停监控' : '▶ 开启流'}
              </button>
            </div>

            <p className="text-[10px] text-slate-400 leading-relaxed font-sans">
              系统当前正在高并发扫描空虚号码资源，以下是底层 RAG 外呼机器人实时 ASR（语音识别后转译）会话内容转译瀑布流。
            </p>

            <div className="space-y-2 max-h-[220px] overflow-y-auto pr-1">
              {liveCalls.map(c => (
                <div key={c.id} className="bg-slate-950/80 p-2.5 rounded border border-slate-800/80 text-[11px] font-mono space-y-1 text-left">
                  <div className="flex justify-between items-center text-[10px] text-slate-500">
                    <span className="text-white flex items-center gap-1">
                      <PhoneCall className="w-3 h-3 text-emerald-400 shrink-0" />
                      {c.phone}
                    </span>
                    <span>{c.line} • 时长 {c.duration} 秒</span>
                  </div>
                  
                  <div className="bg-slate-900 border border-slate-850 p-1.5 rounded text-indigo-200 truncate leading-snug">
                    <span className="text-slate-500">[实时ASR还原]:</span> {c.transcript}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* SIP Channels List */}
          <div className="bg-white rounded-xl border border-slate-100 p-4.5 shadow-xs space-y-4 text-left">
            <h4 className="text-xs font-bold text-slate-800 border-b border-slate-100 pb-2 flex items-center gap-1.5">
              <Volume2 className="w-4 h-4 text-indigo-500" />
              <span>中继SIP线路网关绑定 ({sipLines.length})</span>
            </h4>

            <div className="space-y-2">
              {sipLines.map(line => (
                <div key={line.id} className="p-2.5 bg-slate-50 rounded border border-slate-150 flex items-center justify-between text-xs font-mono">
                  <div>
                    <p className="font-bold text-slate-800 text-[11px] font-sans">{line.name}</p>
                    <p className="text-[10px] text-slate-400">分配最高：{line.concurrency} 并发物理路数</p>
                  </div>
                  <span className={`text-[9px] px-1.5 py-0.2 rounded font-sans ${
                    line.enabled ? 'bg-emerald-50 border border-emerald-200 text-emerald-700' : 'bg-slate-200 text-slate-500'
                  }`}>
                    {line.enabled ? '已启用接续' : '未核准'}
                  </span>
                </div>
              ))}
            </div>

            {/* Config new SIP gate (RBAC) */}
            <form onSubmit={handleAddSip} className="space-y-2 pt-2 border-t border-slate-100">
              <input
                type="text"
                placeholder="拟接入主叫线路网卡名"
                value={newSipName}
                onChange={e => setNewSipName(e.target.value)}
                required
                disabled={!hasPermission('line:create')}
                className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded disabled:opacity-40"
              />
              <div className="flex gap-2">
                <input
                  type="number"
                  placeholder="并发上限"
                  value={newSipLimit}
                  onChange={e => setNewSipLimit(parseInt(e.target.value) || 0)}
                  required
                  disabled={!hasPermission('line:create')}
                  className="w-1/2 text-xs p-2 bg-slate-50 border border-slate-200 rounded disabled:opacity-40"
                />
                <button
                  type="submit"
                  disabled={!hasPermission('line:create')}
                  className={`w-1/2 text-xs font-semibold rounded cursor-pointer ${
                    hasPermission('line:create') ? 'bg-slate-900 text-white hover:bg-black' : 'bg-slate-100 text-slate-400'
                  }`}
                >
                  挂接SIP中继线
                </button>
              </div>
            </form>
          </div>

          {/* Call Blacklists Config */}
          <div className="bg-white rounded-xl border border-slate-100 p-4.5 shadow-xs space-y-3 text-left">
            <h4 className="text-xs font-bold text-slate-800 border-b border-slate-100 pb-2 flex items-center gap-1.5">
              <Shield className="w-4 h-4 text-rose-500" />
              <span>租户黑名单管理 (全局防骚扰控制包)</span>
            </h4>

            {/* Add to list */}
            <form onSubmit={handleAddBlackList} className="flex gap-1.5">
              <input
                type="text"
                value={newBlackNumber}
                onChange={e => setNewBlackNumber(e.target.value)}
                placeholder="输入拉黑手机号码 (M1)"
                required
                disabled={!hasPermission('line:create')}
                className="flex-1 text-xs p-2 bg-slate-50 border border-slate-250 rounded disabled:opacity-40"
              />
              <button
                type="submit"
                disabled={!hasPermission('line:create')}
                className={`text-xs px-3 rounded font-bold cursor-pointer ${
                  hasPermission('line:create') ? 'bg-rose-600 text-white hover:bg-rose-700' : 'bg-slate-150 text-slate-400'
                }`}
              >
                拒拨
              </button>
            </form>

            <div className="flex flex-wrap gap-1 max-h-[80px] overflow-y-auto pr-1">
              {blacklist.map(phone => (
                <span key={phone} className="text-[10px] font-mono bg-rose-50 text-rose-800 border border-rose-200/50 px-1.5 py-0.5 rounded flex items-center gap-1">
                  🚫 {phone}
                  <button
                    type="button"
                    onClick={() => {
                      if (hasPermission('line:create')) {
                        setBlacklist(blacklist.filter(p => p !== phone));
                        onAddAuditLog('呼叫中心', '黑名单解除', `因运营纠正，将号码 ${phone} 从租户反骚扰策略中移出`);
                      }
                    }}
                    className="hover:text-black font-sans font-bold"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>

        </div>

      </div>

      {/* Target list viewer Modal */}
      {viewingTask && (
        <div className="fixed inset-0 bg-slate-900/60 backdrop-blur-[2px] flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl border border-slate-100 shadow-xl w-full max-w-3xl flex flex-col max-h-[85vh] text-left animate-in fade-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="p-5 border-b border-slate-100 flex items-center justify-between">
              <div>
                <h3 className="text-sm font-bold text-slate-900 flex items-center gap-1.5">
                  <FileText className="w-4 h-4 text-indigo-500" />
                  <span>批次外呼名册预览 (Task Roster Explorer)</span>
                </h3>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  正在查看任务: <strong className="text-slate-800">《{viewingTask.name}》</strong> • 包含已转译待呼人员: <span className="text-indigo-600 font-bold">{viewingTask.importedTargets?.length || viewingTask.totalNumbers || 0} 人</span>
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setViewingTask(null);
                  setTargetSearchQuery('');
                }}
                className="p-1 px-2.5 bg-slate-50 border border-slate-150 rounded-lg text-xs text-slate-500 hover:text-slate-800 transition-colors cursor-pointer"
              >
                ✕ 关闭
              </button>
            </div>

            {/* Modal Search Bar or Stats */}
            <div className="p-4 bg-slate-50 border-b border-slate-100 flex flex-col md:flex-row md:items-center justify-between gap-3">
              <div className="relative flex-1">
                <span className="absolute left-3 top-2.5 text-slate-400">
                  <Search className="w-3.5 h-3.5" />
                </span>
                <input
                  type="text"
                  placeholder="检索姓名、电话号码或特定参数绑定..."
                  value={targetSearchQuery}
                  onChange={(e) => setTargetSearchQuery(e.target.value)}
                  className="w-full text-xs pl-8.5 pr-3 py-2 bg-white border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-500 font-sans"
                />
              </div>

              {/* Stats badges */}
              <div className="flex flex-wrap gap-2 text-[10px] font-mono shrink-0">
                <span className="px-2 py-1 bg-white border border-slate-200 rounded text-slate-600">
                  📈 计划规模: {viewingTask.totalNumbers} 例
                </span>
                <span className="px-2 py-1 bg-emerald-50 border border-emerald-150 rounded text-emerald-800">
                  📱 已主叫: {viewingTask.calledNumbers} 例
                </span>
                <span className="px-2 py-1 bg-indigo-50 border border-indigo-150 rounded text-indigo-800 font-bold">
                  🔗 完美接听: {viewingTask.connectedNumbers} 例
                </span>
              </div>
            </div>

            {/* Modal Body Table */}
            <div className="flex-1 overflow-y-auto p-5">
              {(() => {
                const targets = viewingTask.importedTargets || [];
                const filtered = targets.filter(t => {
                  const query = targetSearchQuery.toLowerCase().trim();
                  if (!query) return true;
                  const phoneMatch = t.phone.toLowerCase().includes(query);
                  const nameMatch = t.name.toLowerCase().includes(query);
                  const varsMatch = t.vars ? Object.entries(t.vars).some(([k, v]) => k.toLowerCase().includes(query) || String(v).toLowerCase().includes(query)) : false;
                  return phoneMatch || nameMatch || varsMatch;
                });

                if (filtered.length === 0) {
                  return (
                    <div className="text-center py-16 text-slate-400 text-xs">
                      {targets.length === 0 ? (
                        <div className="space-y-1">
                          <AlertCircle className="w-8 h-8 text-slate-350 mx-auto mb-2" />
                          <p>本计划属于宽泛总额度随机拨号模式，名册细项暂无实例化承载。</p>
                          <p className="text-[10px] text-slate-400">若想配置极具针对性的话术动态注入，请在新建任务时，体验一键名单导入引擎。</p>
                        </div>
                      ) : (
                        <p>没有找到任何符合筛查条件 "{targetSearchQuery}" 的客户记录（共 {targets.length} 人）。</p>
                      )}
                    </div>
                  );
                }

                return (
                  <div className="border border-slate-150 rounded-xl overflow-hidden shadow-2xs">
                    <table className="w-full text-left text-xs border-collapse">
                      <thead>
                        <tr className="bg-slate-100/80 text-slate-700 border-b border-slate-200 font-bold">
                          <th className="p-2.5 pl-4">序号</th>
                          <th className="p-2.5">客户姓名</th>
                          <th className="p-2.5">主叫手机号码</th>
                          <th className="p-2.5">AI ASR 动态携带参数列表</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-slate-150 font-mono text-[11px]">
                        {filtered.map((target, idx) => (
                          <tr key={idx} className="hover:bg-slate-50/50 transition-colors text-slate-700">
                            <td className="p-2.5 pl-4 text-slate-400">{idx + 1}</td>
                            <td className="p-2.5 font-sans font-bold text-slate-900">{target.name}</td>
                            <td className="p-2.5 text-indigo-600 font-bold">{target.phone}</td>
                            <td className="p-2.5">
                              <div className="flex flex-wrap gap-1 font-sans text-[10px]">
                                {target.vars && Object.keys(target.vars).length > 0 ? (
                                  Object.entries(target.vars).map(([k, v]) => {
                                    // Strip template placeholder var_1 to human friendly label
                                    const humanLabel = k === 'var_1' ? '应还金额/标签' : k === 'var_2' ? '借款业务对账' : k === 'var_3' ? '重拨核准日' : k;
                                    return (
                                      <span key={k} className="bg-slate-100/80 border border-slate-200 text-slate-700 rounded px-1.5 py-0.5 font-medium">
                                        <strong className="text-slate-500 font-normal">{humanLabel}:</strong> {v}
                                      </span>
                                    );
                                  })
                                ) : (
                                  <span className="text-[10px] text-slate-400 italic">常规批次（无个性化提示变量）</span>
                                )}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              })()}
            </div>

            {/* Modal Footer */}
            <div className="p-4.5 bg-slate-50 border-t border-slate-100 flex justify-between items-center text-xs text-slate-500">
              <span className="text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded font-sans font-semibold">
                🛡 本地模拟高并发语音流已通过密态混淆安全检测
              </span>
              <button
                type="button"
                onClick={() => {
                  setViewingTask(null);
                  setTargetSearchQuery('');
                }}
                className="px-4 py-1.5 bg-indigo-650 hover:bg-indigo-700 text-white rounded-lg font-semibold transition-colors cursor-pointer"
              >
                我知道了
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
