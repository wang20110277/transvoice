import {
  Tenant,
  Permission,
  Role,
  User,
  PromptTemplate,
  KnowledgeBase,
  CallTask,
  CallRecord,
  MemoryProfile,
  AuditLog
} from '../types';

export const INITIAL_TENANTS: Tenant[] = [
  {
    id: 'tenant-fin',
    name: '星河消费金融有限公司',
    logo: 'S',
    status: 'active',
    expiredAt: '2027-12-31 23:59:59',
    tier: 'Ultimate',
    maxConcurrentCalls: 120,
    monthlyMinutesQuota: 500000,
    monthlyMinutesUsed: 238410
  },
  {
    id: 'tenant-med',
    name: '智灵人民医院智能呼叫中心',
    logo: 'M',
    status: 'active',
    expiredAt: '2026-10-01 12:00:00',
    tier: 'Enterprise',
    maxConcurrentCalls: 30,
    monthlyMinutesQuota: 100000,
    monthlyMinutesUsed: 42120
  },
  {
    id: 'tenant-eco',
    name: '乐尚精品售后商城',
    logo: 'E',
    status: 'active',
    expiredAt: '2026-05-15 18:00:00',
    tier: 'Standard',
    maxConcurrentCalls: 15,
    monthlyMinutesQuota: 50000,
    monthlyMinutesUsed: 12450
  }
];

// Real system permissions registry
export const ALL_PERMISSIONS: Permission[] = [
  // Prompt menu permissions
  { code: 'menu:prompt', name: '菜单：提示词管理', category: '提示词管理', description: '控制左侧提示词菜单栏可见性', type: 'menu' },
  { code: 'prompt:view', name: '按钮：查看提示词', category: '提示词管理', description: '查看提示词列表及详情内容', type: 'button' },
  { code: 'prompt:create', name: '按钮：创建提示词', category: '提示词管理', description: '新增/克隆提示词话术模板', type: 'button' },
  { code: 'prompt:update', name: '按钮：修改提示词', category: '提示词管理', description: '修改已有提示词模板及管理版本', type: 'button' },
  { code: 'prompt:delete', name: '按钮：删除提示词', category: '提示词管理', description: '删除暂未使用的提示词模板', type: 'button' },
  { code: 'prompt:test', name: '按钮：联调大模型测试', category: '提示词管理', description: '调用AI调试终端，输入变量验证效果', type: 'button' },

  // KB menu permissions
  { code: 'menu:kb', name: '菜单：知识库管理', category: '知识库管理', description: '控制知识库管理菜单可见性', type: 'menu' },
  { code: 'kb:view', name: '按钮：查看知识库', category: '知识库管理', description: '查看知识库目录、配置及文档库', type: 'button' },
  { code: 'kb:create', name: '按钮：创建知识库', category: '知识库管理', description: '新建一个或多个独立的语义向量库', type: 'button' },
  { code: 'kb:delete', name: '按钮：删除知识库', category: '知识库管理', description: '彻底销毁某一主题知识库', type: 'button' },
  { code: 'kbDoc:upload', name: '按钮：上传知识文档', category: '知识库管理', description: '向现有知识库上传PDF/docx并启动切片', type: 'button' },
  { code: 'kbDoc:delete', name: '按钮：删除知识文档', category: '知识库管理', description: '移除知识库中关联的某篇原文档', type: 'button' },
  { code: 'kbSearch:query', name: '按钮：检索沙箱测试', category: '知识库管理', description: '测试关键词和向量匹配度召回效果', type: 'button' },

  // Call Center menu permissions
  { code: 'menu:callCenter', name: '菜单：呼叫中心', category: '呼叫中心', description: '查看呼叫策略及实时呼出大盘', type: 'menu' },
  { code: 'callTask:view', name: '按钮：查看任务', category: '呼叫中心', description: '查看所有外呼计划的参数及接通进度', type: 'button' },
  { code: 'callTask:create', name: '按钮：创建外呼任务', category: '呼叫中心', description: '导入号码清单配置语料与拨打参数启动任务', type: 'button' },
  { code: 'callTask:control', name: '按钮：启停控制', category: '呼叫中心', description: '运行、挂起或强行阻断外呼任务', type: 'button' },
  { code: 'callTask:delete', name: '按钮：删除任务', category: '呼叫中心', description: '清理已结束的历史外呼任务记录', type: 'button' },
  { code: 'line:view', name: '按钮：查看SIP通道', category: '呼叫中心', description: '查看SIP主叫及运营商网关并发配置', type: 'button' },
  { code: 'line:create', name: '按钮：配置线路网关', category: '呼叫中心', description: '添加/编辑底层外呼线路和并发频次限制', type: 'button' },

  // Call Records menu permissions
  { code: 'menu:cdr', name: '菜单：呼叫记录', category: '呼叫记录', description: '控制通话明细及录音质检菜单可见性', type: 'menu' },
  { code: 'cdr:view', name: '按钮：查看呼叫明细', category: '呼叫记录', description: '检索筛选所有外呼通话的终结指标与转写', type: 'button' },
  { code: 'cdr:export', name: '按钮：导出呼出数据', category: '呼叫记录', description: '将通话指标大表一键打包导出Excel', type: 'button' },
  { code: 'recording:play', name: '按钮：在线录音试听', category: '呼叫记录', description: '拉取对象存储中的通话波形音频并播放', type: 'button' },
  { code: 'recording:download', name: '按钮：录音包下载', category: '呼叫记录', description: '直接拉取MP3/WAV音频源文件进行备份', type: 'button' },
  { code: 'qa:label', name: '按钮：质检标注打标', category: '呼叫记录', description: '对机器人对话表现打分，人工校准意向分值', type: 'button' },

  // Memory system menu
  { code: 'menu:memory', name: '菜单：记忆系统', category: '记忆系统', description: '控制记忆库侧边菜单页可见性', type: 'menu' },
  { code: 'memoryStrategy:view', name: '按钮：查看记忆规则', category: '记忆系统', description: '阅览有哪些特征字段需要动态提取覆盖', type: 'button' },
  { code: 'memoryStrategy:create', name: '按钮：制定提取规则', category: '记忆系统', description: '构建长期特征和用户偏好写回模板', type: 'button' },
  { code: 'profile:view', name: '按钮：参阅客户画像', category: '记忆系统', description: '查看通话沉淀的业务画像', type: 'button' },
  { code: 'memory:edit', name: '按钮：人工标记纠偏', category: '记忆系统', description: '人工更正或补充长期记忆库画像内容', type: 'button' },

  // System Safety Permissions
  { code: 'menu:settings', name: '菜单：安全设置', category: '系统安全', description: '控制安全设置与团队权限菜单可见性', type: 'menu' },
  { code: 'user:create', name: '按钮：创建与修改用户', category: '系统安全', description: '管理团队内成员账号及分配所属岗位角色', type: 'button' },
  { code: 'role:create', name: '按钮：配制独立权限矩阵', category: '系统安全', description: '自主编辑、配置和增删系统角色的细分功能权限', type: 'button' }
];

export const INITIAL_ROLES: Role[] = [
  // Tenant Financial Roles
  {
    id: 'role-fin-admin',
    tenantId: 'tenant-fin',
    name: '租户系统管理员',
    description: '持有整套外呼策略、团队权限和业务底盘的全项管辖权。',
    permissions: ALL_PERMISSIONS.map(p => p.code),
    isSystem: true
  },
  {
    id: 'role-fin-operator',
    tenantId: 'tenant-fin',
    name: '电销主管',
    description: '日常管理话术模版、编排外呼名单并监控并发线路。',
    permissions: [
      'menu:prompt', 'prompt:view', 'prompt:create', 'prompt:update', 'prompt:test',
      'menu:kb', 'kb:view', 'kbDoc:upload',
      'menu:callCenter', 'callTask:view', 'callTask:create', 'callTask:control',
      'menu:cdr', 'cdr:view', 'recording:play',
      'menu:memory', 'profile:view'
    ],
    isSystem: false
  },
  {
    id: 'role-fin-qa',
    tenantId: 'tenant-fin',
    name: '催收质检员',
    description: '评阅呼叫详单以及ASR转写，人工微调风险等级和标记纠偏。',
    permissions: [
      'menu:cdr', 'cdr:view', 'recording:play', 'recording:download', 'qa:label',
      'menu:memory', 'profile:view', 'memory:edit'
    ],
    isSystem: false
  },
  {
    id: 'role-fin-viewer',
    tenantId: 'tenant-fin',
    name: '客服代表',
    description: '对通话概览和标签画像只读审计，没有配置修改权。',
    permissions: [
      'menu:cdr', 'cdr:view', 'recording:play'
    ],
    isSystem: false
  },
  {
    id: 'role-fin-viewall',
    tenantId: 'tenant-fin',
    name: '特邀只读大审计官',
    description: '特邀全局观察岗位，允许跨模块查看系统内全部对话模型、知识库、呼叫任务、详单和记忆画像，但屏蔽所有写/改行为。',
    permissions: [
      'menu:prompt', 'prompt:view',
      'menu:kb', 'kb:view',
      'menu:callCenter', 'callTask:view', 'line:view',
      'menu:cdr', 'cdr:view', 'recording:play',
      'menu:memory', 'memoryStrategy:view', 'profile:view',
      'menu:settings'
    ],
    isSystem: false
  },
  {
    id: 'role-fin-security',
    tenantId: 'tenant-fin',
    name: '安全合规管理员',
    description: '专职于安全审计与团队成员权限独立分配，可自定义配置权限分配矩阵并管理用户。',
    permissions: [
      'menu:settings',
      'user:create',
      'role:create',
      'menu:cdr',
      'cdr:view'
    ],
    isSystem: false
  },

  // Tenant Med Roles
  {
    id: 'role-med-admin',
    tenantId: 'tenant-med',
    name: '医疗机构超管',
    description: '监管诊后康复、定期体检外呼及出院回访规则。',
    permissions: ALL_PERMISSIONS.map(p => p.code),
    isSystem: true
  },
  {
    id: 'role-med-doc',
    tenantId: 'tenant-med',
    name: '随访医生',
    description: '配置健康教育包、核阅随访任务和录音日志。',
    permissions: [
      'menu:prompt', 'prompt:view', 'prompt:test',
      'menu:kb', 'kb:view', 'kbDoc:upload',
      'menu:callCenter', 'callTask:view', 'callTask:create', 'callTask:control',
      'menu:cdr', 'cdr:view', 'recording:play',
      'menu:memory', 'profile:view'
    ],
    isSystem: false
  },

  // Tenant Eco Roles
  {
    id: 'role-eco-admin',
    tenantId: 'tenant-eco',
    name: '商城超级管理员',
    description: '对乐尚电商售后回访系统、话术演进、物流评价全权设置。',
    permissions: ALL_PERMISSIONS.map(p => p.code),
    isSystem: true
  },
  {
    id: 'role-eco-cs',
    tenantId: 'tenant-eco',
    name: '客服组长',
    description: '管理售后满意度模板、抽检回访和包裹跟单。',
    permissions: [
      'menu:prompt', 'prompt:view', 'prompt:update',
      'menu:kb', 'kb:view',
      'menu:cdr', 'cdr:view', 'recording:play', 'qa:label'
    ],
    isSystem: false
  }
];

export const INITIAL_USERS: User[] = [
  // Tenant Financial Users
  {
    id: 'user-fin-1',
    tenantId: 'tenant-fin',
    username: 'alex_fincore',
    realName: '钟敏敏',
    email: 'zhongmm@fincore.com',
    roleId: 'role-fin-admin',
    status: 'active',
    createdAt: '2025-12-01 09:30:00',
    deptId: 'all'
  },
  {
    id: 'user-fin-2',
    tenantId: 'tenant-fin',
    username: 'li_operator',
    realName: '李建华',
    email: 'lijh@fincore.com',
    roleId: 'role-fin-operator',
    status: 'active',
    createdAt: '2026-01-10 10:30:00',
    deptId: 'telemarketing'
  },
  {
    id: 'user-fin-3',
    tenantId: 'tenant-fin',
    username: 'zhang_qa',
    realName: '张敏捷',
    email: 'zhangmj@fincore.com',
    roleId: 'role-fin-qa',
    status: 'active',
    createdAt: '2026-02-15 11:15:00',
    deptId: 'collection'
  },
  {
    id: 'user-fin-4',
    tenantId: 'tenant-fin',
    username: 'wang_viewer',
    realName: '王小芬',
    email: 'wangxf@fincore.com',
    roleId: 'role-fin-viewer',
    status: 'active',
    createdAt: '2026-03-01 14:00:00',
    deptId: 'customer_service'
  },
  {
    id: 'user-wang-viewer',
    tenantId: 'tenant-fin',
    username: 'wang_observer',
    realName: '王先生 (特邀只读官)',
    email: 'wang20110277@gmail.com',
    roleId: 'role-fin-viewall',
    status: 'active',
    createdAt: '2026-06-18 10:00:00',
    deptId: 'customer_service'
  },
  {
    id: 'user-wang-editor',
    tenantId: 'tenant-fin',
    username: 'wang_admin',
    realName: '王先生 (超级运营官)',
    email: 'wang20110277@gmail.com',
    roleId: 'role-fin-admin',
    status: 'active',
    createdAt: '2026-06-18 10:01:00',
    deptId: 'all'
  },
  {
    id: 'user-wang-security',
    tenantId: 'tenant-fin',
    username: 'wang_security',
    realName: '王先生 (权限合规官)',
    email: 'wang20110277@gmail.com',
    roleId: 'role-fin-security',
    status: 'active',
    createdAt: '2026-06-18 10:02:00',
    deptId: 'all'
  },

  // Tenant Medical Users
  {
    id: 'user-med-1',
    tenantId: 'tenant-med',
    username: 'dr_chen',
    realName: '陈医生',
    email: 'chen.doc@medcare.org',
    roleId: 'role-med-admin',
    status: 'active',
    createdAt: '2025-10-01 08:30:00',
    deptId: 'all'
  },
  {
    id: 'user-med-2',
    tenantId: 'tenant-med',
    username: 'nurse_li',
    realName: '李静',
    email: 'li.nurse@medcare.org',
    roleId: 'role-med-doc',
    status: 'active',
    createdAt: '2025-11-20 09:00:00',
    deptId: 'customer_service'
  },

  // Tenant Ecommerce Users
  {
    id: 'user-eco-1',
    tenantId: 'tenant-eco',
    username: 'retail_admin',
    realName: '林经理',
    email: 'lin.mgr@lefashion.com',
    roleId: 'role-eco-admin',
    status: 'active',
    createdAt: '2025-11-01 10:00:00',
    deptId: 'all'
  },
  {
    id: 'user-eco-2',
    tenantId: 'tenant-eco',
    username: 'shuang_cs',
    realName: '董双双',
    email: 'dongss@lefashion.com',
    roleId: 'role-eco-cs',
    status: 'active',
    createdAt: '2025-12-15 11:30:00',
    deptId: 'customer_service'
  },
  {
    id: 'user-wang-med',
    tenantId: 'tenant-med',
    username: 'wang_healthcare',
    realName: '王先生 (医疗首席安全官)',
    email: 'wang20110277@gmail.com',
    roleId: 'role-med-admin',
    status: 'active',
    createdAt: '2026-06-18 10:05:00',
    deptId: 'all'
  },
  {
    id: 'user-wang-eco',
    tenantId: 'tenant-eco',
    username: 'wang_retail',
    realName: '王先生 (商城联合创始人)',
    email: 'wang20110277@gmail.com',
    roleId: 'role-eco-admin',
    status: 'active',
    createdAt: '2026-06-18 10:06:00',
    deptId: 'all'
  }
];

export const INITIAL_PROMPTS: PromptTemplate[] = [
  // Tenant Financial
  {
    id: 'prompt-fin-1',
    tenantId: 'tenant-fin',
    title: '微秒贷逾期还款提醒-温和告知版',
    category: '催收',
    content: '你是一家名为星河小贷的专属虚拟AI催收助手。当前你的呼叫对象是借款客户【{customer_name}】。本次致电目的是友好地提醒对方已经逾期【{arrears_days}】天。未缴金额为【{amount}】元。\n\n【开场白要求】：\n\"你好，请问是【{customer_name}】本人吗？我是星河金融的还款贴心告知官，工号AI2045。本次来电不为施压，旨在协助提醒您，您的微秒贷已经滞纳【{arrears_days}】天了，逾期金额累计为【{amount}】元。可能导致征信降级。我们为您今天减免了手续费，请问您今天最晚能在哪点前通过我们的APP处理呢？\"\n\n【引导方向】：\n1. 若客户诉求困难，提供分期政策释疑。\n2. 若客户表示打错电话，及时核对号码并记录，不再复盘追访。',
    variables: ['customer_name', 'arrears_days', 'amount'],
    version: 'V1.4',
    updatedAt: '2026-05-12 14:32:10',
    updatedBy: '钟敏敏',
    history: [
      { version: 'V1.3', content: '早期直接告知，无手续费减免口径。效果偏强硬。', updatedAt: '2026-04-10 11:20:00', updatedBy: '李建华' },
      { version: 'V1.2', content: '测试原始版催收提示。', updatedAt: '2026-03-01 09:12:00', updatedBy: '李建华' }
    ]
  },
  {
    id: 'prompt-fin-2',
    tenantId: 'tenant-fin',
    title: '信贷授信高意向用户激活活动话术',
    category: '意向回访',
    content: '你是一家名为星河科技金融的电话客户经理AI。该客户【{customer_name}】此前在我们小程序中完成了实名评估，获得预授信额度高达【{credit_limit}】万元，特批低年化利率分期权益。\n\n【开场白】：\n\"喂，您好【{customer_name}】经理，我是星河普惠的客服小智呀。恭喜您！系统今天审批通过了您的专享金借款名额，授信金额为【{credit_limit}】万大额，首期还能领到免息体验券。不需要任何抵押。请问您手头最近会有经营或日常周转型资金需求吗？\"\n\n【对话关键】：\n提取并记忆：1. 是否有需求（需要/不需要）； 2. 预计额度需求； 3. 是否加微沟通。',
    variables: ['customer_name', 'credit_limit'],
    version: 'V2.1',
    updatedAt: '2026-06-02 08:44:12',
    updatedBy: '李建华',
    history: []
  },

  // Tenant Medical
  {
    id: 'prompt-med-1',
    tenantId: 'tenant-med',
    title: '全科门诊手术出院患者24h健康复查回访',
    category: '满意度调查',
    content: '你是智灵人民医院智能关怀AI护士【{nurse_name}】。根据临床数据，出院患者【{customer_name}】于昨天完成了全科日间小手术，现进行出院后第一天常规康复追踪。\n\n【基本使命】：\n向患者确认是否按医嘱服药、伤口是否有明显渗血或剧痛，以及是否有发热大于38.5度的情况。\n\n【开场白】：\n\"您好【{customer_name}】先生/女士，我是智灵医院住院部AI随访护士【{nurse_name}】。昨天您刚完成出院，现在打电话耽误您两分钟，想特别关心下您今天伤口恢复状况怎么样？请问您有按时吃消炎药吗，体温有没有测过，发烧吗？\"\n\n【处理分支】：\n若患者告知有38.5度以上高烧，立刻通过记忆写入红色警报标签，并主动告知科室24小时医生值班电话。',
    variables: ['nurse_name', 'customer_name'],
    version: 'V1.0.3',
    updatedAt: '2026-06-01 10:20:00',
    updatedBy: '李静',
    history: []
  },

  // Tenant Ecommerce
  {
    id: 'prompt-eco-1',
    tenantId: 'tenant-eco',
    title: '极速送达确认与包裹运输服务评价',
    category: '满意度调查',
    content: '你是乐尚VIP旗舰店智能履约评价小助手。本次通话是为了跟买家【{customer_name}】核查最近购买的【{product_name}】是否已完好拆包收到，并对物流员【{courier_name}】评价。\n\n【话术内容】：\n\"亲亲您好，我是乐尚精品商城售后主管。看到系统显示您的【{product_name}】上午由顺丰小哥【{courier_name}】派送完成啦！耽误您10秒，包裹我们打包封盒严实吗？另外小哥有没有敲门派送到家，还是放进了驿站呢？如果有任何磕坏或不满意，我们立刻提供退货包运费补偿哦！\"\n\n【策略】：引导五星好评，如遇破损直接转接高级人工介入。',
    variables: ['customer_name', 'product_name', 'courier_name'],
    version: 'V3.0',
    updatedAt: '2026-06-15 17:30:11',
    updatedBy: '董双双',
    history: []
  }
];

export const INITIAL_KNOWLEDGE: KnowledgeBase[] = [
  // Tenant Financial
  {
    id: 'kb-fin-1',
    tenantId: 'tenant-fin',
    name: '星河普惠用户放贷及催收违约法务常见Q&A',
    description: '此知识库为催收AI外呼提供对合规抗辩、逾期宽限期、诉讼仲裁以及征信黑名单疑问的解答依据目录。',
    status: 'active',
    docCount: 2,
    docs: [
      {
        id: 'doc-fin-1-1',
        name: '互联网金融小额信贷还款宽限准则与征信上报期说明书.pdf',
        size: '1.42 MB',
        uploadTime: '2026-01-15 09:20:00',
        status: 'indexed',
        chunkCount: 14,
        chunks: [
          '还款宽限期统一为应还日后第3天24点前，在此期间全额补足不产生逾期罚息，亦不向央行征信系统上报逾期点迹。',
          '对于超过5天仍未足额还款且无法取得有效合理辩护的借款，星河金融将启动常态化每日法治提示，严重者可能移交法务提起联合诉讼。'
        ]
      },
      {
        id: 'doc-fin-1-2',
        name: '星河普惠分期延期利息计算公式及免息券赠送条款.docx',
        size: '645 KB',
        uploadTime: '2026-02-18 10:14:12',
        status: 'indexed',
        chunkCount: 6,
        chunks: [
          '满足首期全额还清客户，可依申请减免第2期、第3期手续复利，最高免息额度不得过还款额的10%。',
          '对于失实投诉及特困扶助对象（低保/重症住院凭证），客服有权在系统开通为期最晚30天的停催延期绿色保障期。'
        ]
      }
    ]
  },

  // Tenant Medical
  {
    id: 'kb-med-1',
    tenantId: 'tenant-med',
    name: '全科科室出院康复与防跌倒家庭护理指导库',
    description: '提供给随访AI，在应对患者关于术后切口护理、沐浴禁忌、术后用药副作用和饮食限制时的标准权威医学解惑。',
    status: 'active',
    docCount: 1,
    docs: [
      {
        id: 'doc-med-1-1',
        name: '外科日间手术门诊切口换药与红肿发热居家处理共识.pdf',
        size: '2.84 MB',
        uploadTime: '2026-04-01 11:30:00',
        status: 'indexed',
        chunkCount: 22,
        chunks: [
          '术后72小时内切口轻微胀痛或有少许淡红色渗出液属正常临床指征。若见脓性混浊分泌物或局部皮肤灼热剧烈红肿，需及时返回医院门诊复诊。',
          '出院口服抗生素请严格遵医嘱足疗程。如产生严重腹泻或皮疹发痒，请立刻停药并联系主管护师换药。'
        ]
      }
    ]
  },

  // Tenant Ecommerce
  {
    id: 'kb-eco-1',
    tenantId: 'tenant-eco',
    name: '乐尚旗舰大促退换货极速理赔细则及优惠券补偿库',
    description: '应对客户反馈包裹运输破损、少件漏发或尺码买错时，话术小助手提取的赔付口径。',
    status: 'active',
    docCount: 1,
    docs: [
      {
        id: 'doc-eco-1-1',
        name: '乐尚全店七天无理由极速双向免运费退换货指引.txt',
        size: '24 KB',
        uploadTime: '2026-05-18 16:22:00',
        status: 'indexed',
        chunkCount: 3,
        chunks: [
          '对于运输途中造成的商品由于抛揉损毁、包装开裂，不经复杂核验启动退款，并在12小时内安排顺丰快递上门取回。',
          '非质量问题仅是不喜欢，仍支持全额退还，运费险由乐尚尊贵黑金卡会员权益代缴。'
        ]
      }
    ]
  }
];

export const INITIAL_TASKS: CallTask[] = [
  // Tenant Financial Tasks
  {
    id: 'task-fin-1',
    tenantId: 'tenant-fin',
    name: '【批量呼叫】微秒贷逾期M1阶段法制提醒任务（周一高发期）',
    promptId: 'prompt-fin-1',
    kbIds: ['kb-fin-1'],
    status: 'running',
    totalNumbers: 1250,
    calledNumbers: 480,
    connectedNumbers: 290,
    concurrentLimit: 200,
    startTime: '2026-06-16 09:00:00',
    redialStrategy: { maxRetries: 3, intervalMinutes: 120 },
    allowedHours: '09:00-12:00, 14:00-20:00',
    importedTargets: [
      { phone: '13812345678', name: '王大锤', vars: { var_1: '500.00元', var_2: '微秒贷M1A卷' } },
      { phone: '15911112222', name: '赵铁柱', vars: { var_1: '1,200.00元', var_2: '微秒贷极速版' } },
      { phone: '13344445555', name: '李美丽', vars: { var_1: '850.00元', var_2: '闪电大额授信' } },
      { phone: '18677778888', name: '诸葛冷酷', vars: { var_1: '3,100.00元', var_2: '微秒贷公积金分期' } }
    ]
  },
  {
    id: 'task-fin-2',
    tenantId: 'tenant-fin',
    name: '【意向激活】618周年大额提额授信呼叫项目',
    promptId: 'prompt-fin-2',
    kbIds: ['kb-fin-1'],
    status: 'paused',
    totalNumbers: 8000,
    calledNumbers: 3600,
    connectedNumbers: 2120,
    concurrentLimit: 350,
    startTime: '2026-06-10 10:00:00',
    redialStrategy: { maxRetries: 1, intervalMinutes: 1440 },
    allowedHours: '09:30-11:30, 14:30-18:00',
    importedTargets: [
      { phone: '17755554444', name: '钱多多', vars: { var_1: '50,000元额度', var_2: '618白名单特邀' } },
      { phone: '13566667777', name: '孙悟空', vars: { var_1: '20,000元额度', var_2: '常规提额邀约' } }
    ]
  },

  // Tenant Medical Tasks
  {
    id: 'task-med-1',
    tenantId: 'tenant-med',
    name: '全科每日出院日间术后随访患者关怀（智能排队）',
    promptId: 'prompt-med-1',
    kbIds: ['kb-med-1'],
    status: 'running',
    totalNumbers: 85,
    calledNumbers: 42,
    connectedNumbers: 38,
    concurrentLimit: 15,
    startTime: '2026-06-17 08:30:00',
    redialStrategy: { maxRetries: 4, intervalMinutes: 60 },
    allowedHours: '09:00-11:45, 14:30-17:30',
    importedTargets: [
      { phone: '13988889999', name: '周大妈', vars: { var_1: '骨科关节镜手术', var_2: '康复防跌倒指南' } },
      { phone: '15012345678', name: '李爷爷', vars: { var_1: '术后白内障置换', var_2: '避免强光直射' } },
      { phone: '18888882222', name: '满月婴儿刘家宝宝', vars: { var_1: '一周岁接种回访', var_2: '儿保预防接种' } }
    ]
  },

  // Tenant Ecommerce Tasks
  {
    id: 'task-eco-1',
    tenantId: 'tenant-eco',
    name: '乐尚天猫大单派件签收回访反馈项目',
    promptId: 'prompt-eco-1',
    kbIds: ['kb-eco-1'],
    status: 'completed',
    totalNumbers: 240,
    calledNumbers: 240,
    connectedNumbers: 215,
    concurrentLimit: 10,
    startTime: '2026-06-15 09:00:00',
    endTime: '2026-06-15 17:00:00',
    redialStrategy: { maxRetries: 2, intervalMinutes: 180 },
    allowedHours: '10:00-13:00, 15:00-19:00',
    importedTargets: [
      { phone: '13111113333', name: '苏小北', vars: { var_1: '乐尚夏季碎花拼接长裙', var_2: '顺丰王小二' } },
      { phone: '15252528888', name: '叶子姐', vars: { var_1: '超薄无缝束腰防晒服', var_2: '京东刘快跑' } }
    ]
  }
];

export const INITIAL_RECORDS: CallRecord[] = [
  // Tenant Financial Records
  {
    id: 'cdr-fin-1',
    tenantId: 'tenant-fin',
    taskId: 'task-fin-1',
    taskName: '【批量呼叫】微秒贷逾期M1阶段法制提醒任务（周一高发期）',
    customerPhone: '13812345678',
    customerName: '李强',
    status: 'connected',
    durationSeconds: 124,
    hangupReason: '呼叫对象主动挂断',
    aiSpentCost: 0.28,
    intentTag: 'high_interest',
    qaAuditStatus: 'unreviewed',
    createdAt: '2026-06-17 14:22:15',
    audioUrl: 'https://sample-videos.com/audio/mp3/wave.mp3',
    messages: [
      { sender: 'ai', text: '您好，请问是李强先生本人吗？我是星河金融的还款贴心告知官，工号AI2045。您的微秒贷已经滞纳5天了，逾期金额累计为1250元，我们将全力协助您解决征信降级危机，今天特别申请在18点前帮你免除手续罚息。请问您今天几点前能通过APP处理呢？', timestamp: '14:22:18' },
      { sender: 'customer', text: '啊？星河小贷是吧。怎么我都逾期了啊，我记得我前两天不是已经付过了吗？是不是弄错了？', timestamp: '14:22:30', intent: '抗辩_已还款', sentiment: 'neutral' },
      { sender: 'ai', text: '李强先生，我们能为您进行二次在账核实。不过现在我们系统确实没有入账这条交易。请问您是否当时在假期跨行汇款，或者把资金打错到了别的托管卡呢？', timestamp: '14:23:05' },
      { sender: 'customer', text: '哦，我想起来了，我钱在那个交通银行卡里，今天限额好像没刷过去！那我一会3点钟开会结束了立刻在你们APP上用支付宝重新还！这次算我按时还的话，利息能直接在单子里免掉是吧？', timestamp: '14:23:45', intent: '承诺还款', sentiment: 'positive' },
      { sender: 'ai', text: '没问题，李先生！为您记录在案，最晚承诺还款时间为今天下午17:00前。在APP操作时，直接勾选本息合并抵扣券即可无手续费豁免，非常感谢您的配合，祝您下午顺心。', timestamp: '14:24:10' }
    ]
  },
  {
    id: 'cdr-fin-2',
    tenantId: 'tenant-fin',
    taskId: 'task-fin-1',
    taskName: '【批量呼叫】微秒贷逾期M1阶段法制提醒任务（周一高发期）',
    customerPhone: '15988889999',
    customerName: '黄建军',
    status: 'connected',
    durationSeconds: 45,
    hangupReason: '呼叫对象辱骂情绪化挂断',
    aiSpentCost: 0.12,
    intentTag: 'refusal',
    qaAuditStatus: 'rectified',
    qaComments: '机器人语气过于平稳，未对客户失业困难做适当同理心降级，导致激化矛盾挂断，需优化降温话术。',
    createdAt: '2026-06-17 15:10:02',
    audioUrl: 'https://sample-videos.com/audio/mp3/violent.mp3',
    messages: [
      { sender: 'ai', text: '您好，请问是黄建军先生吗？我是星河金融AI催收前置告知岗。您现在累计逾期金额达3400元，请问今天何时能处理该呆账？', timestamp: '15:10:05' },
      { sender: 'customer', text: '我现在没饭吃了！工作丢了，你们一天到晚打电话有完没完！等老子有钱了能不还吗？天天催逼个毛啊！不要再打了！', timestamp: '15:10:20', intent: '抗辩_失业困难', sentiment: 'negative' },
      { sender: 'ai', text: '黄先生，我们非常理解您当下面临的困难。不过逾期会使您的央行征信报告上留下重大黑点，甚至影响家人贷款购买大件。为了防止更深损失，您可以试向亲友借用，今天做一笔最低还款。', timestamp: '15:10:35' },
      { sender: 'customer', text: '借个鬼！滚！别再特么骚扰了！再打电话我直接去监管局投诉你们！挂了！', timestamp: '15:10:45', intent: '拒绝沟通', sentiment: 'negative' }
    ]
  },
  {
    id: 'cdr-fin-3',
    tenantId: 'tenant-fin',
    taskId: 'task-fin-2',
    taskName: '【意向激活】618周年大额提额授信呼叫项目',
    customerPhone: '17655556666',
    customerName: '苏珊娜',
    status: 'unanswered',
    durationSeconds: 0,
    hangupReason: '忙音 / 无应答挂断',
    aiSpentCost: 0.0,
    intentTag: 'unknown',
    qaAuditStatus: 'unreviewed',
    createdAt: '2026-06-17 11:40:00',
    messages: []
  },

  // Tenant Medical Records
  {
    id: 'cdr-med-1',
    tenantId: 'tenant-med',
    taskId: 'task-med-1',
    taskName: '全科每日出院日间术后随访患者关怀（智能排队）',
    customerPhone: '13599990000',
    customerName: '赵自强',
    status: 'connected',
    durationSeconds: 88,
    hangupReason: '正常通话结束',
    aiSpentCost: 0.22,
    intentTag: 'high_interest',
    qaAuditStatus: 'passed',
    createdAt: '2026-06-17 10:14:30',
    audioUrl: 'https://sample-videos.com/audio/mp3/medical.mp3',
    messages: [
      { sender: 'ai', text: '您好赵自强先生，我是智灵人民医院门诊部的智能随访关怀护士李静。昨天您完成日间息肉小切除已经出院返回。请问您今天感觉胃黏膜或切口疼痛明显吗？有按时遵照陈医生的指示服用消炎药吗？', timestamp: '10:14:35' },
      { sender: 'customer', text: '奥，是李护士啊。我按时吃药了，就是感觉肚子老是有点发胀，隐隐约约有一丝小隐痛，请问我今天中午能吃稀饭配酸菜吗？老伴儿有点担心。', timestamp: '10:14:55', intent: '询问食物限制', sentiment: 'neutral' },
      { sender: 'ai', text: '赵先生，伤口处轻微发胀属于恢复期的典型波动，请保持关注。针对您的饮食：术后首日适宜温凉且极软烂易消化的白粥，但千万【不推荐】搭配过咸或发霉腌制的酸菜或辣酱哦，以免引发强胃酸刺激产生出血。可以改用爽口的清蒸南瓜或鸡蛋羹。', timestamp: '10:15:30' },
      { sender: 'customer', text: '哎好，怪不得呢。那辛亏你们提醒，不然老伴那咸酸菜差点害了我的胃。行，我们中午喝白米粥。我温度刚量过是36.6度，没有发烧。谢谢医院打来关心，这技术真好！', timestamp: '10:15:48', intent: '满意感谢', sentiment: 'positive' }
    ]
  },

  // Tenant Ecommerce Records
  {
    id: 'cdr-eco-1',
    tenantId: 'tenant-eco',
    taskId: 'task-eco-1',
    taskName: '乐尚天猫大单派件签收回访反馈项目',
    customerPhone: '18533334444',
    customerName: '周小雅',
    status: 'connected',
    durationSeconds: 70,
    hangupReason: '正常通话结束',
    aiSpentCost: 0.15,
    intentTag: 'high_interest',
    qaAuditStatus: 'unreviewed',
    createdAt: '2026-06-15 11:20:00',
    messages: [
      { sender: 'ai', text: '亲亲您好，我是乐尚名媛女装服饰售后小助理。看到系统显示您的【赫本风蕾丝碎花泡泡袖裙】今天上午已经由顺丰小哥派送到家。向您悄悄核查，这件衣服包装破损了吗？您上身合不合身，喜欢我们极速空运的时效吗？', timestamp: '11:20:05' },
      { sender: 'customer', text: '收到啦，你们这空运太速度了，昨天下单今天一早就拿到手。材质摸起来很高级。就是腰部这里偏紧了一丢丢，不知道能不能换成L码呀？', timestamp: '11:20:25', intent: '求助换货', sentiment: 'positive' },
      { sender: 'ai', text: '周女士，没关系，衣服不合适可以为您急速换货！根据我们的七天无理由双向包邮规则，我一会儿发一条【专属换大一码链接】到您的绑定手机上。您点进去即可预约快递免费在今天下午上门把原件拿走。新的L码我们今天立刻重新发顺丰出库。您看这样安排可以吗？', timestamp: '11:20:48' },
      { sender: 'customer', text: '哇！那太省心了！你们效率太太赞了！没问题，我就等你的短信连接啦。谢谢！', timestamp: '11:21:05', intent: '极度满意', sentiment: 'positive' }
    ]
  }
];

export const INITIAL_MEMORIES: MemoryProfile[] = [
  // Tenant Financial Memorires
  {
    id: 'mem-fin-1',
    tenantId: 'tenant-fin',
    phone: '13812345678',
    customerName: '李强',
    gender: '男',
    lastIntent: '意向_承诺今天17点前通过支付宝还款',
    tags: ['还款信用良好', '逾期催收', '白领阶级'],
    longTermMemory: {
      arrears_reason: '交通银行薪资代发卡扣款限额超标，当天未即刻通过，导致虚空跳期。',
      preferred_repay_channel: '支付宝APP内直接扣划',
      authorized_contact_time: '15:00之后（避开核心会议时段）',
      next_repay_promise: '2026-06-17 17:00:00'
    },
    sessionMemoryLogs: [
      { taskId: 'task-fin-1', timestamp: '2026-06-17 14:24:10', summary: 'AI前置提醒正常沟通。客户辩解称是卡限额未刷过成功，已详细引导并承诺于今日五点前通过本息代扣券还款1250元，客户表达感谢。评分高意向。' },
      { taskId: 'task-fin-1', timestamp: '2026-06-10 11:32:00', summary: '首期扣款，客户主动询问是否有跨行免手续费手段。引导了APP内联通支付方案。' }
    ]
  },
  {
    id: 'mem-fin-2',
    tenantId: 'tenant-fin',
    phone: '15988889999',
    customerName: '黄建军',
    gender: '男',
    lastIntent: '抗辩_重度失业困难、拒绝配合并威胁诉求保监局',
    tags: ['严重逾期', '易怒情绪', '诉讼风控边缘', '已加入二次防骚扰红线'],
    longTermMemory: {
      financial_hardship_cause: '于2026年4月份外贸厂精简员工中被直接遣散，至今无固定现金入账。',
      communication_barrier: '拒绝提供任何证明，表现出对外呼电话反感和骂骂咧咧。',
      legal_dispute_risk: '高，声称已经针对每日2次的外呼频度报备给了本地金融办及司法热线。'
    },
    sessionMemoryLogs: [
      { taskId: 'task-fin-1', timestamp: '2026-06-17 15:10:45', summary: '逾期催款致电。刚接通即出现极高负面情绪抗拒，借口失业没有经济来源破口大骂，坚决拒不沟通。AI已按敏感风控自动静音收场，并将通话划归为异常难缠。建议后续派驻专家线人工安抚，规避越级监管层投诉。' }
    ]
  },

  // Tenant Medical Memories
  {
    id: 'mem-med-1',
    tenantId: 'tenant-med',
    phone: '13599990000',
    customerName: '赵自强',
    gender: '男',
    lastIntent: '康复正常_已按医嘱服用消炎药，体温常规正常，已告知咸菜饮食禁忌。',
    tags: ['恢复极佳', '顺从医嘱', '需要清咸高油防呆提示'],
    longTermMemory: {
      post_op_status: '日间内息肉小手术康复首日，无渗血无发热。36.6℃。',
      diet_mistake_risk: '高。因家属准备稀饭且原计划加入腌酸菜，被随访AI及时阻止，现已置换成蒸蛋羹和南瓜粥。',
      caregiver_phone: '配偶同住照料。'
    },
    sessionMemoryLogs: [
      { taskId: 'task-med-1', timestamp: '2026-06-17 10:15:48', summary: '出院24小时常规健康回访。患者无异常疼痛，遵医嘱好、有发胀微调，想违吃咸酸菜被AI及时拦免。客户十分感谢医院的主动人文关怀，并对智能外呼表示满意。' }
    ]
  }
];

export const INITIAL_AUDITS: AuditLog[] = [
  {
    id: 'audit-1',
    tenantId: 'tenant-fin',
    username: 'alex_fincore',
    module: '提示词管理',
    action: '创建提示词',
    ip: '112.98.45.101',
    createdAt: '2026-06-17 10:05:12',
    details: '新建提示词模板《微秒贷逾期还款提醒-温和告知版》，主张友好减免口径。'
  },
  {
    id: 'audit-2',
    tenantId: 'tenant-fin',
    username: 'li_operator',
    module: '呼叫中心',
    action: '暂停外呼任务',
    ip: '112.98.45.105',
    createdAt: '2026-06-17 14:40:00',
    details: '挂起‘618周年大额提额授信呼叫项目’任务，原因：线路运营商并发限频临时调整。'
  },
  {
    id: 'audit-3',
    tenantId: 'tenant-med',
    username: 'dr_chen',
    module: '知识库管理',
    action: '触发文档向量化',
    ip: '120.33.12.89',
    createdAt: '2026-06-17 09:20:00',
    details: '上传文件‘外科日间手术门诊切口换药与红肿发热居家处理共识.pdf’并完成22个语义分片向量编制。'
  }
];

export const PLATFORM_CONFIG = {
  templateMenus: [
    { code: 'menu:prompt', name: '提示词管理', isDefault: true },
    { code: 'menu:kb', name: '知识库管理', isDefault: true },
    { code: 'menu:callCenter', name: '呼叫中心', isDefault: true },
    { code: 'menu:cdr', name: '呼叫记录', isDefault: true },
    { code: 'menu:memory', name: '记忆系统', isDefault: true },
    { code: 'menu:settings', name: '系统管理', isDefault: true }
  ]
};
