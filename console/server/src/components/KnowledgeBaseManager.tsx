import React, { useState } from 'react';
import { KnowledgeBase, KbDoc, AuditLog } from '../types';
import { Database, Upload, Trash2, Search, Sliders, ChevronRight, FileText, CheckCircle2, RotateCw, BookOpen } from 'lucide-react';

interface KnowledgeBaseManagerProps {
  knowledgeBases: KnowledgeBase[];
  activeTenantId: string;
  hasPermission: (code: string) => boolean;
  onAddKB: (kb: KnowledgeBase) => void;
  onDeleteKB: (id: string) => void;
  onUpdateKB: (kb: KnowledgeBase) => void;
  onAddAuditLog: (module: string, action: string, details: string) => void;
}

export default function KnowledgeBaseManager({
  knowledgeBases,
  activeTenantId,
  hasPermission,
  onAddKB,
  onDeleteKB,
  onUpdateKB,
  onAddAuditLog,
}: KnowledgeBaseManagerProps) {
  const tenantKBs = knowledgeBases.filter(kb => kb.tenantId === activeTenantId);

  // States
  const [selectedKB, setSelectedKB] = useState<KnowledgeBase | null>(tenantKBs[0] || null);
  const [selectedDoc, setSelectedDoc] = useState<KbDoc | null>(null);

  // New KB Name Form
  const [isCreatingKB, setIsCreatingKB] = useState(false);
  const [newKBName, setNewKBName] = useState('');
  const [newKBDesc, setNewKBDesc] = useState('');

  // Search Sandbox States
  const [searchQuery, setSearchQuery] = useState('');
  const [similarityThreshold, setSimilarityThreshold] = useState(0.65);
  const [selectedAlgorithm, setSelectedAlgorithm] = useState<'embedding' | 'hybrid' | 'text'>('embedding');
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState<{ text: string; score: number; sourceDoc: string }[]>([]);

  // File upload drag-and-drop state
  const [isDragging, setIsDragging] = useState(false);
  const [uploadProgressList, setUploadProgressList] = useState<{ name: string; progress: number; status: 'indexing' | 'completed' }[]>([]);

  // Create new KB (RBAC checked)
  const handleCreateKB = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKBName.trim() || !hasPermission('kb:create')) return;

    const newKB: KnowledgeBase = {
      id: `kb-${Date.now()}`,
      tenantId: activeTenantId,
      name: newKBName,
      description: newKBDesc || '企业通用专享知识库，为智能外呼语音AI提供解答。',
      status: 'active',
      docCount: 0,
      docs: []
    };

    onAddKB(newKB);
    onAddAuditLog('知识库管理', '创建知识库', `创建全新语义知识目录库：《${newKBName}》`);
    setSelectedKB(newKB);
    setNewKBName('');
    setNewKBDesc('');
    setIsCreatingKB(false);
  };

  // Mock File dropping & parsing trigger
  const handleFileUpload = (fileName: string, fileSize: string) => {
    if (!selectedKB || !hasPermission('kbDoc:upload')) return;

    const taskName = fileName;
    setUploadProgressList(prev => [...prev, { name: taskName, progress: 10, status: 'indexing' }]);

    onAddAuditLog('知识库管理', '上传文档分切片', `向《${selectedKB.name}》传输文档 ${taskName} 并触发分布式 OCR及语义分片解析流程`);

    // Simulate stepping of indexing progress
    let steps = 10;
    const interval = setInterval(() => {
      steps += 30;
      if (steps >= 100) {
        clearInterval(interval);
        
        // Inject parsed document into current active KB state
        const isPdf = taskName.toLowerCase().endsWith('.pdf');
        const count = isPdf ? 18 : 6;
        const newDoc: KbDoc = {
          id: `doc-${Date.now()}`,
          name: taskName,
          size: fileSize,
          uploadTime: new Date().toISOString().replace('T', ' ').substring(0, 19),
          status: 'indexed',
          chunkCount: count,
          chunks: isPdf ? [
            '【分片#1】本核心准则适用于本机构主发卡在还款日后第三天24点前完成转款的特殊延期手续豁免。逾期滞延需有合理证明。',
            '【分片#2】若客户自述有遭遇地方降雨、失业等阻力，需标记系统将由后续复评主管介入宽限免催安排。',
            '【分片#3】患者咨询本药品使用时，应当明确告知出院首日一次服用2粒，温水服下。产生胃胀痛立刻停。',
            '【分片#4】七天无理由退款包运费细则：收包后保持挂牌完好、洗刷未损毁的前提下可安排急速换货，快递直发顺丰退回。'
          ] : [
            '【分片#1】首期免息卷可凭还款通知短信直接在APP客户端点击激活。',
            '【分片#2】针对中老年人居家防跌倒护理，卫生间须常备防滑垫，并在起坐区增加扶手支点。'
          ]
        };

        const updatedKB: KnowledgeBase = {
          ...selectedKB,
          docCount: selectedKB.docCount + 1,
          docs: [newDoc, ...selectedKB.docs]
        };

        onUpdateKB(updatedKB);
        setSelectedKB(updatedKB);
        setUploadProgressList(prev => prev.filter(x => x.name !== taskName));
      } else {
        setUploadProgressList(prev => prev.map(x => x.name === taskName ? { ...x, progress: steps } : x));
      }
    }, 800);
  };

  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const onDragLeave = () => {
    setIsDragging(false);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    if (!hasPermission('kbDoc:upload')) return;
    const file = e.dataTransfer.files[0];
    if (file) {
      const sizeStr = file.size > 1024 * 1024 
        ? `${(file.size / (1024 * 1024)).toFixed(2)} MB` 
        : `${Math.round(file.size / 1024)} KB`;
      handleFileUpload(file.name, sizeStr);
    }
  };

  // Run Semantic retrieve matching simulator
  const handleRetrieveTest = () => {
    if (!selectedKB || !searchQuery.trim()) return;
    setIsSearching(true);

    setTimeout(() => {
      // Gather all chunks from all docs inside selected KB
      const allChunks: { text: string; source: string }[] = [];
      selectedKB.docs.forEach(doc => {
        doc.chunks.forEach(chunk => {
          allChunks.push({ text: chunk, source: doc.name });
        });
      });

      // Filter based on mock scoring
      const results = allChunks.map(item => {
        // Simple heuristic score based on query text overlap or preset matching
        const containsWord = searchQuery.split('').some(char => item.text.includes(char));
        let score = containsWord ? 0.72 + Math.random() * 0.22 : 0.35 + Math.random() * 0.25;
        // Cap score
        if (score > 0.99) score = 0.98;
        return {
          text: item.text,
          score: parseFloat(score.toFixed(2)),
          sourceDoc: item.source
        };
      })
      .filter(x => x.score >= similarityThreshold)
      .sort((a, b) => b.score - a.score);

      setSearchResults(results);
      setIsSearching(false);
      onAddAuditLog('知识库管理', '检索沙箱自测', `对归档知识库 《${selectedKB.name}》 进行了语义匹配性抽测试，检索词： '${searchQuery}'`);
    }, 1000);
  };

  // Confirm delete doc (RBAC checked)
  const handleDeleteDoc = (docId: string) => {
    if (!selectedKB || !hasPermission('kbDoc:delete')) return;
    const updatedDocs = selectedKB.docs.filter(d => d.id !== docId);
    const updatedKB: KnowledgeBase = {
      ...selectedKB,
      docCount: updatedDocs.length,
      docs: updatedDocs
    };
    onUpdateKB(updatedKB);
    setSelectedKB(updatedKB);
    setSelectedDoc(null);
    onAddAuditLog('知识库管理', '移除知识文档', `在《${selectedKB.name}》下彻底删除了文献节点。`);
  };

  return (
    <div className="space-y-6">
      {/* Top section overview */}
      <div className="bg-white p-4.5 rounded-xl border border-slate-100 shadow-xs flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h2 className="text-base font-bold text-slate-800 flex items-center gap-1.5"><Database className="w-5 h-5 text-indigo-600" /> 知识语料库向量化管理器（RAG Base）</h2>
          <p className="text-xs text-slate-500 mt-1">上传企业规章、专业医护手册等政策背景，系统将自动进行分片与向量化模型建档，供给外呼AI提取精确话术解答依据。</p>
        </div>

        {/* Create new KB button (RBAC) */}
        {!isCreatingKB ? (
          <button
            onClick={() => {
              if (hasPermission('kb:create')) setIsCreatingKB(true);
            }}
            disabled={!hasPermission('kb:create')}
            className={`cursor-pointer px-4 py-2 text-xs font-semibold rounded-lg shrink-0 transition-colors ${
              hasPermission('kb:create')
                ? 'bg-indigo-600 text-white hover:bg-indigo-700'
                : 'bg-slate-100 text-slate-400 cursor-not-allowed'
            }`}
          >
            + 创建新知识库
          </button>
        ) : (
          <form onSubmit={handleCreateKB} className="flex gap-2 w-full md:w-auto">
            <input
              type="text"
              value={newKBName}
              onChange={e => setNewKBName(e.target.value)}
              placeholder="拟知识库名称"
              className="text-xs p-2 border border-slate-200 bg-slate-50 rounded"
              required
            />
            <button type="submit" className="bg-emerald-600 hover:bg-emerald-700 text-white text-xs px-3 rounded font-bold">确定</button>
            <button type="button" onClick={() => setIsCreatingKB(false)} className="bg-slate-150 text-slate-600 text-xs px-2.5 rounded font-bold">取消</button>
          </form>
        )}
      </div>

      {/* Main split workbench layout */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        
        {/* Left Column: KB directories (Col span 4/12) */}
        <div className="lg:col-span-4 space-y-4">
          <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-xs space-y-2 h-[260px] overflow-y-auto">
            <span className="text-xs font-bold text-slate-700 block border-b border-slate-100 pb-2 mb-2">已加载的向量空间 ({tenantKBs.length}个)</span>
            
            {tenantKBs.length === 0 ? (
              <p className="text-slate-400 text-xs text-center py-10">租户库名下无任何向量语库</p>
            ) : (
              tenantKBs.map(kb => {
                const isActive = selectedKB?.id === kb.id;
                return (
                  <div
                    key={kb.id}
                    onClick={() => {
                      setSelectedKB(kb);
                      setSelectedDoc(null);
                    }}
                    className={`p-3 rounded-lg border text-left cursor-pointer transition-all flex justify-between items-center ${
                      isActive 
                        ? 'border-indigo-600 bg-indigo-50/20 shadow-xs' 
                        : 'border-slate-150 hover:bg-slate-50'
                    }`}
                  >
                    <div className="space-y-0.5 pr-2">
                      <h4 className="text-xs font-bold text-slate-800 line-clamp-1">{kb.name}</h4>
                      <p className="text-[10px] text-slate-500 line-clamp-1">{kb.description}</p>
                    </div>
                    <span className="text-[10px] font-mono shrink-0 bg-slate-100 text-slate-600 border border-slate-200 px-1.5 py-0.5 rounded">
                      {kb.docCount} 篇
                    </span>
                  </div>
                );
              })
            )}
          </div>

          {/* Sandbox retrieval sandbox test workspace */}
          <div className="bg-white rounded-xl border border-slate-100 p-4 shadow-xs space-y-3">
            <div className="flex items-center gap-1.5 text-xs font-bold text-slate-700 border-b border-slate-100 pb-2 mb-1">
              <Search className="w-4 h-4 text-indigo-500" />
              <span>智能语义召回调试沙箱</span>
            </div>

            <div className="space-y-3 text-xs">
              <div className="space-y-1">
                <label className="text-[10px] text-slate-500 font-bold block">拟模拟检索关联提问</label>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder="例：逾期几天会上信用报告？"
                  className="w-full text-xs p-2 bg-slate-50 border border-slate-200 rounded focus:outline-none focus:border-indigo-500"
                />
              </div>

              {/* Slider for matching index */}
              <div className="space-y-1">
                <div className="flex justify-between text-[10px] text-slate-500">
                  <span className="font-bold flex items-center gap-0.5"><Sliders className="w-3 h-3" /> 检索相关性阈值阈值</span>
                  <span className="font-mono text-indigo-600 font-bold">{similarityThreshold.toFixed(2)}</span>
                </div>
                <input
                  type="range"
                  min="0.30"
                  max="0.95"
                  step="0.05"
                  value={similarityThreshold}
                  onChange={e => setSimilarityThreshold(parseFloat(e.target.value))}
                  className="w-full h-1 bg-slate-200 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                />
              </div>

              {/* Algorithm select */}
              <div className="space-y-1">
                <label className="text-[10px] text-slate-500 font-bold block">匹配检索引擎算法</label>
                <div className="grid grid-cols-3 gap-1.5 text-[10px] font-semibold">
                  <button
                    type="button"
                    onClick={() => setSelectedAlgorithm('embedding')}
                    className={`p-1.5 rounded border ${
                      selectedAlgorithm === 'embedding' ? 'bg-indigo-50 border-indigo-500 text-indigo-700' : 'bg-slate-50 border-slate-200 text-slate-600'
                    }`}
                  >
                    向量Embedding
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedAlgorithm('hybrid')}
                    className={`p-1.5 rounded border ${
                      selectedAlgorithm === 'hybrid' ? 'bg-indigo-50 border-indigo-500 text-indigo-700' : 'bg-slate-50 border-slate-200 text-slate-600'
                    }`}
                  >
                    混合检索
                  </button>
                  <button
                    type="button"
                    onClick={() => setSelectedAlgorithm('text')}
                    className={`p-1.5 rounded border ${
                      selectedAlgorithm === 'text' ? 'bg-indigo-50 border-indigo-500 text-indigo-700' : 'bg-slate-50 border-slate-200 text-slate-600'
                    }`}
                  >
                    Sparse 文本
                  </button>
                </div>
              </div>

              <button
                type="button"
                onClick={handleRetrieveTest}
                disabled={isSearching || !selectedKB || !searchQuery.trim()}
                className="w-full bg-slate-900 border border-slate-800 text-white font-bold p-2.5 rounded-lg hover:bg-slate-850 text-xs transition-colors flex items-center justify-center gap-1 disabled:opacity-50"
              >
                {isSearching ? <RotateCw className="w-3.5 h-3.5 animate-spin" /> : '匹配度过滤检索测试'}
              </button>
            </div>
          </div>
        </div>

        {/* Right Column: KB documents & Chunk fragments (Col span 8/12) */}
        <div className="lg:col-span-8 flex flex-col gap-6">
          
          {selectedKB ? (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 bg-white p-5 rounded-xl border border-slate-100 shadow-xs flex-1">
              {/* Left Subsection: Files management */}
              <div className="space-y-4">
                <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                  <h3 className="text-xs font-bold text-slate-800 font-sans">1. 归档文本文件管理 ({selectedKB.docs.length} 篇)</h3>
                  <span className="text-[10px] text-slate-400">已自动同步至底层存储</span>
                </div>

                {/* Drag-and-drop drag zone (RBAC) */}
                <div
                  onDragOver={onDragOver}
                  onDragLeave={onDragLeave}
                  onDrop={onDrop}
                  className={`border-2 border-dashed rounded-xl p-5 text-center transition-all ${
                    isDragging ? 'border-indigo-600 bg-indigo-50/20' : 'border-slate-200 hover:border-slate-300'
                  } ${!hasPermission('kbDoc:upload') ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer'}`}
                >
                  <Upload className="w-8 h-8 mx-auto text-indigo-500 mb-2 stroke-1" />
                  <p className="text-xs font-semibold text-slate-700">拖拽上传本地业务标准、Q&A 说明文档</p>
                  <p className="text-[10px] text-slate-400 mt-1">支持包含 PDF, Word, TXT 格式分块向量化</p>
                  
                  {/* File browser fallback trigger button */}
                  <label className="mt-3 inline-block cursor-pointer">
                    <span className={`text-[10px] font-bold px-3 py-1.5 rounded-md text-white ${
                      hasPermission('kbDoc:upload') ? 'bg-indigo-600 hover:bg-indigo-700' : 'bg-slate-200 text-slate-400 cursor-not-allowed'
                    }`}>
                      选择本地文件
                    </span>
                    <input
                      type="file"
                      className="hidden"
                      disabled={!hasPermission('kbDoc:upload')}
                      onChange={e => {
                        const file = e.target.files?.[0];
                        if (file) {
                          const sizeStr = file.size > 1024 * 1024 
                            ? `${(file.size / (1024 * 1024)).toFixed(2)} MB` 
                            : `${Math.round(file.size / 1024)} KB`;
                          handleFileUpload(file.name, sizeStr);
                        }
                      }}
                    />
                  </label>
                </div>

                {/* Upload progresses */}
                {uploadProgressList.map(up => (
                  <div key={up.name} className="p-2.5 bg-slate-50 border border-slate-100 rounded-lg text-xs space-y-1.5">
                    <div className="flex justify-between items-center text-[10px]">
                      <span className="font-medium text-slate-700 truncate max-w-[150px]">{up.name}</span>
                      <span className="text-indigo-600 font-mono font-bold">后台向量分片解析 {up.progress}%</span>
                    </div>
                    <div className="w-full bg-slate-200 rounded-full h-1 overflow-hidden">
                      <div className="bg-indigo-600 h-1 rounded-full transition-all" style={{ width: `${up.progress}%` }} />
                    </div>
                  </div>
                ))}

                {/* Docs list items */}
                <div className="space-y-2 max-h-[180px] overflow-y-auto pr-1">
                  {selectedKB.docs.map(doc => {
                    const isDocSelected = selectedDoc?.id === doc.id;
                    return (
                      <div
                        key={doc.id}
                        onClick={() => setSelectedDoc(doc)}
                        className={`p-3 rounded-lg border text-left cursor-pointer transition-all flex items-center justify-between ${
                          isDocSelected ? 'border-indigo-600 bg-slate-50' : 'border-slate-150 hover:bg-slate-50/50'
                        }`}
                      >
                        <div className="flex items-center gap-2 pr-2">
                          <FileText className="w-4 h-4 text-indigo-500 shrink-0" />
                          <div>
                            <h5 className="text-[11px] font-bold text-slate-800 line-clamp-1 leading-tight">{doc.name}</h5>
                            <p className="text-[10px] text-slate-400 mt-0.5">{doc.size} • 导入于 {doc.uploadTime.split(' ')[0]}</p>
                          </div>
                        </div>

                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className="text-[9px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1">{doc.chunkCount} 个切片</span>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleDeleteDoc(doc.id);
                            }}
                            disabled={!hasPermission('kbDoc:delete')}
                            className="text-slate-400 hover:text-rose-600 disabled:opacity-40"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Right Subsection: Split paragraphs view or search results sandbox output */}
              <div className="space-y-4 border-t md:border-t-0 md:border-l md:pl-6 border-slate-150 text-left">
                {/* Dynamically either show active document chunks, OR sandbox search matches */}
                {searchResults.length > 0 ? (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                      <h3 className="text-xs font-bold text-slate-850 flex items-center gap-1"><BookOpen className="w-3.5 h-3.5 text-indigo-600" /> API召回命中切片</h3>
                      <button
                        onClick={() => setSearchResults([])}
                        className="text-[10px] text-indigo-600 font-bold bg-indigo-50 border border-indigo-150 hover:bg-indigo-100 px-2 rounded"
                      >
                        清除测试匹配
                      </button>
                    </div>

                    <div className="space-y-3.5 max-h-[340px] overflow-y-auto pr-1">
                      {searchResults.map((res, i) => (
                        <div key={i} className="p-3 bg-indigo-50/40 rounded-xl space-y-1.5 border border-indigo-100/50 text-xs">
                          <div className="flex justify-between items-center text-[9px] text-slate-400 font-mono">
                            <span className="text-slate-500 font-bold bg-indigo-100/80 px-1 py-0.2 rounded truncate max-w-[120px]">{res.sourceDoc}</span>
                            <span>相关性打分: <strong className="text-indigo-600 font-semibold">{res.score}</strong></span>
                          </div>
                          <p className="text-slate-700 leading-relaxed font-mono text-[11px] bg-white p-2 rounded border border-indigo-50">
                            {res.text}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : selectedDoc ? (
                  <div className="space-y-3">
                    <div className="flex justify-between items-center border-b border-slate-100 pb-2">
                      <h3 className="text-xs font-bold text-slate-800 line-clamp-1">2.《{selectedDoc.name}》的语义拆分分片段明细</h3>
                      <span className="text-[10px] text-emerald-600 flex items-center gap-0.5"><CheckCircle2 className="w-3 h-3" /> 已向量化</span>
                    </div>

                    <div className="space-y-2.5 max-h-[340px] overflow-y-auto pr-1 text-xs">
                      {selectedDoc.chunks.map((ck, i) => (
                        <div key={i} className="p-3 bg-slate-50 border border-slate-150 rounded-lg font-mono text-slate-600 leading-relaxed text-[11px]">
                          {ck}
                        </div>
                      ))}
                    </div>
                  </div>
                ) : (
                  <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400 space-y-2 select-none h-[340px]">
                    <Database className="w-10 h-10 stroke-1" />
                    <div>
                      <h4 className="text-slate-700 font-semibold text-xs">2. 展开语义切段库/调试预览</h4>
                      <p className="text-[11px] text-slate-400 max-w-xs mt-1">
                        请在左列点击其中一篇文献查看预制的字句片段，或者在左下角沙盒测试里直接发起一个全库提问。
                      </p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="bg-white p-12 text-center rounded-xl border border-slate-100 text-slate-400 text-xs">
              无加载库，请在左侧新建知识库语，或切换租户。
            </div>
          )}

        </div>

      </div>
    </div>
  );
}
