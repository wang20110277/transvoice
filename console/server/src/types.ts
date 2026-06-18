export interface Tenant {
  id: string;
  name: string;
  logo: string;
  status: 'active' | 'suspended' | 'expired';
  expiredAt: string;
  tier: 'Standard' | 'Enterprise' | 'Ultimate';
  maxConcurrentCalls: number;
  monthlyMinutesQuota: number;
  monthlyMinutesUsed: number;
}

export interface Permission {
  code: string;
  name: string;
  category: string;
  description: string;
  type: 'menu' | 'button' | 'api';
}

export interface Role {
  id: string;
  tenantId: string;
  name: string;
  description: string;
  permissions: string[]; // List of permission codes
  isSystem?: boolean;
}

export interface User {
  id: string;
  tenantId: string;
  username: string;
  realName: string;
  email: string;
  roleId: string;
  status: 'active' | 'inactive';
  createdAt: string;
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all' | 'security';
}

export interface PromptTemplate {
  id: string;
  tenantId: string;
  title: string;
  category: string; // e.g., '催收', '意向回访', '满意度调查'
  content: string;
  variables: string[]; // e.g., ['customer_name', 'arrears_days', 'amount']
  version: string;
  updatedAt: string;
  updatedBy: string;
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all';
  history?: {
    version: string;
    content: string;
    updatedAt: string;
    updatedBy: string;
  }[];
}

export interface KbDoc {
  id: string;
  name: string;
  size: string;
  uploadTime: string;
  status: 'indexed' | 'indexing' | 'failed';
  chunkCount: number;
  chunks: string[];
}

export interface KnowledgeBase {
  id: string;
  tenantId: string;
  name: string;
  description: string;
  status: 'active' | 'inactive';
  docCount: number;
  docs: KbDoc[];
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all';
}

export interface ImportedCallTarget {
  phone: string;
  name: string;
  vars?: { [key: string]: string };
}

export interface CallTask {
  id: string;
  tenantId: string;
  name: string;
  promptId: string;
  kbIds: string[];
  status: 'idle' | 'running' | 'paused' | 'completed';
  totalNumbers: number;
  calledNumbers: number;
  connectedNumbers: number;
  concurrentLimit: number;
  startTime: string;
  endTime?: string;
  redialStrategy: {
    maxRetries: number;
    intervalMinutes: number;
  };
  allowedHours: string; // e.g., "09:00-12:00, 14:00-18:00"
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all';
  importedTargets?: ImportedCallTarget[];
}

export interface ChatMessage {
  sender: 'ai' | 'customer';
  text: string;
  timestamp: string;
  intent?: string;
  sentiment?: 'positive' | 'neutral' | 'negative';
}

export interface CallRecord {
  id: string;
  tenantId: string;
  taskId: string;
  taskName: string;
  customerPhone: string;
  customerName: string;
  status: 'connected' | 'unanswered' | 'rejected' | 'busy' | 'failed';
  durationSeconds: number;
  hangupReason: string;
  aiSpentCost: number;
  intentTag: 'high_interest' | 'mild_interest' | 'refusal' | 're_contact' | 'unknown';
  messages: ChatMessage[];
  qaAuditStatus: 'unreviewed' | 'passed' | 'rectified';
  qaComments?: string;
  audioUrl?: string; // Mock playback audio source
  createdAt: string;
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all';
}

export interface MemoryProfile {
  id: string;
  tenantId: string;
  phone: string;
  customerName: string;
  gender: string;
  lastIntent: string;
  tags: string[];
  longTermMemory: { [key: string]: any };
  sessionMemoryLogs: {
    taskId: string;
    timestamp: string;
    summary: string;
  }[];
  deptId?: 'telemarketing' | 'customer_service' | 'collection' | 'all';
}

export interface AuditLog {
  id: string;
  tenantId: string;
  username: string;
  module: string;
  action: string;
  ip: string;
  createdAt: string;
  details: string;
}
